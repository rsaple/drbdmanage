#!/usr/bin/python
"""
    drbdmanage - management of distributed DRBD9 resources
    Copyright (C) 2013, 2014   LINBIT HA-Solutions GmbH
                               Author: R. Altnoeder

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

import sys
import os
import signal
import time
import gobject
import subprocess
import fcntl
import errno
import logging
import logging.handlers
import re
import traceback
import inspect
import StringIO

from drbdmanage.consts import (
    SERIAL, NODE_NAME, NODE_ADDR, NODE_AF, RES_NAME, RES_PORT, VOL_MINOR,
    DEFAULT_VG, SERVER_CONFFILE, KEY_DRBDCTRL_VG, DRBDCTRL_DEFAULT_PORT,
    DRBDCTRL_RES_NAME, DRBDCTRL_RES_FILE, DRBDCTRL_RES_PATH, RES_PORT_NR_AUTO,
    RES_PORT_NR_ERROR, FLAG_OVERWRITE, FLAG_DISCARD, FLAG_DISKLESS,
    FLAG_CONNECT
)
from drbdmanage.utils import NioLineReader, CmdLineReader, MetaData
from drbdmanage.utils import (
    build_path, extend_path, generate_secret, get_free_number, plugin_import,
    add_rc_entry, serial_filter, props_filter, string_to_bool,
    split_main_aux_props, aux_props_selector
)
from drbdmanage.exceptions import (
    DM_DEBUG, DM_ECTRLVOL, DM_EEXIST, DM_EINVAL,DM_EMINOR, DM_ENAME,
    DM_ENODECNT, DM_ENODEID, DM_ENOENT, DM_EPERSIST, DM_EPLUGIN, DM_EPORT,
    DM_ESECRETG, DM_ESTORAGE, DM_EVOLID, DM_EVOLSZ, DM_ENOTIMPL, DM_SUCCESS
)
from drbdmanage.exceptions import (
    InvalidMinorNrException, InvalidNameException, PersistenceException,
    PluginException, SyntaxException, VolSizeRangeException, AbortException,
    dm_exc_text
)
from drbdmanage.drbd.drbdcore import (
    Assignment, DrbdManager, DrbdNode, DrbdResource, DrbdVolume,
    DrbdVolumeState
)
from drbdmanage.snapshots.snapshots import (
    DrbdSnapshot, DrbdSnapshotAssignment, DrbdSnapshotVolumeState
)
from drbdmanage.drbd.persistence import persistence_impl
from drbdmanage.storage.storagecore import BlockDeviceManager, MinorNr
from drbdmanage.conf.conffile import ConfFile, DrbdAdmConf


class DrbdManageServer(object):

    """
    drbdmanage server - main class
    """

    DM_VERSION = "0.20"

    EVT_UTIL = "drbdsetup"

    EVT_TYPE_CHANGE = "change"
    EVT_TYPE_EXISTS = "exists"
    EVT_SRC_CON     = "connection"
    EVT_SRC_PEERDEV = "peer-device"
    EVT_SRC_RES     = "resource"
    EVT_ARG_NAME    = "name"
    EVT_ARG_ROLE    = "role"
    EVT_ARG_REPL    = "replication"
    EVT_ARG_CON     = "connection"

    EVT_ROLE_PRIMARY   = "Primary"
    EVT_ROLE_SECONDARY = "Secondary"

    EVT_REPL_SYNCTARGET = "SyncTarget"

    # Sleep times (in seconds) for various stages of the
    # events subprocess termination loop
    EVT_TERM_SLEEP_SHORT = 0.5
    EVT_TERM_SLEEP_LONG  = 2

    LOGGING_FORMAT = "drbdmanaged[%(process)d]: %(levelname)-10s %(message)s"

    KEY_STOR_NAME      = "storage-plugin"
    KEY_DEPLOYER_NAME  = "deployer-plugin"
    KEY_MAX_NODE_ID    = "max-node-id"
    KEY_MAX_PEERS      = "max-peers"
    KEY_MIN_MINOR_NR   = "min-minor-nr"
    KEY_MIN_PORT_NR    = "min-port-nr"
    KEY_MAX_PORT_NR    = "max-port-nr"

    KEY_DRBDADM_PATH   = "drbdadm-path"
    KEY_EXTEND_PATH    = "extend-path"
    KEY_DRBD_CONFPATH  = "drbd-conf-path"

    DEFAULT_MAX_NODE_ID  =   31
    DEFAULT_MAX_PEERS    =    7
    DEFAULT_MIN_MINOR_NR =  100
    DEFAULT_MIN_PORT_NR  = 7000
    DEFAULT_MAX_PORT_NR  = 7999

    # defaults
    CONF_DEFAULTS = {
        KEY_STOR_NAME      : "drbdmanage.storage.lvm.LVM",
        KEY_DEPLOYER_NAME  : "drbdmanage.deployers.BalancedDeployer",
        KEY_MAX_NODE_ID    : str(DEFAULT_MAX_NODE_ID),
        KEY_MAX_PEERS      : str(DEFAULT_MAX_PEERS),
        KEY_MIN_MINOR_NR   : str(DEFAULT_MIN_MINOR_NR),
        KEY_MIN_PORT_NR    : str(DEFAULT_MIN_PORT_NR),
        KEY_MAX_PORT_NR    : str(DEFAULT_MAX_PORT_NR),
        KEY_DRBDADM_PATH   : "/usr/sbin",
        KEY_EXTEND_PATH    : "/sbin:/usr/sbin:/bin:/usr/bin",
        KEY_DRBD_CONFPATH  : "/var/lib/drbd.d",
        KEY_DRBDCTRL_VG    : DEFAULT_VG
    }

    # BlockDevice manager
    _bd_mgr    = None
    # Configuration objects maps
    _nodes     = None
    _resources = None
    # Events log pipe
    _evt_file  = None
    # RegEx pattern for events parsing
    _evt_pat   = re.compile(r'(?P<type>\w+) (?P<source>[\w-]+)(?P<attrs>.*)')
    # Subprocess handle for the events log source
    _proc_evt  = None
    # Reader for the events log
    _reader    = None
    # Event handler for incoming data
    _evt_in_h  = None
    # Event handler for the hangup event on the subprocess pipe
    _evt_hup_h = None

    # The name of the node this server is running on
    _instance_node_name = None

    # The hash of the currently loaded configuration
    _conf_hash = None

    # Server configuration
    _conf      = None

    # Logging
    _root_logger = None
    DM_LOGLEVELS = {
      "CRITICAL" : logging.CRITICAL,
      "ERROR"    : logging.ERROR,
      "WARNING"  : logging.WARNING,
      "INFO"     : logging.INFO,
      "DEBUG"    : logging.DEBUG
    }

    # Global drbdmanage cluster configuration
    _cluster_conf         = {}
    _cluster_conf[SERIAL] = 1

    # Change generation flag; controls updates of the serial number
    _change_open  = False

    # DEBUGGING FLAGS
    dbg_events = False


    def __init__(self):
        """
        Initialize and start up the drbdmanage server
        """
        # The "(unknown)" node name never matches, because brackets are not
        # allowed characters in node names
        self._instance_node_name = "(unknown)"
        if len(sys.argv) >= 2:
            self._instance_node_name = sys.argv[1]
        else:
            try:
                uname = os.uname()
                if len(uname) >= 2:
                    self._instance_node_name = uname[1]
            except Exception:
                pass
        self.init_logging()
        logging.info("DRBDmanage server, version %s"
                     " -- initializing on node '%s'"
                     % (self.DM_VERSION, self._instance_node_name))
        self._nodes     = {}
        self._resources = {}
        # load the server configuration file
        self.load_server_conf()
        # ensure that the PATH environment variable is set up
        extend_path(self.get_conf_value(self.KEY_EXTEND_PATH))
        self._bd_mgr    = BlockDeviceManager(self._conf[self.KEY_STOR_NAME])
        self._drbd_mgr  = DrbdManager(self)
        self._drbd_mgr.adjust_drbdctrl()
        # load the drbdmanage database from the control volume
        self.load_conf()
        # start up the resources deployed by drbdmanage on the current node
        self._drbd_mgr.initial_up()
        try:
            self.init_events()
        except (OSError, IOError):
            logging.critical("failed to initialize drbdsetup events tracing, "
                             "aborting startup")
            exit(1)
        # update storage pool information if it is unknown
        inst_node = self.get_instance_node()
        if inst_node is not None:
            poolsize = inst_node.get_poolsize()
            poolfree = inst_node.get_poolfree()
            if poolsize == -1 or poolfree == -1:
                self.update_pool([])


    def run(self):
        """
        drbdmanage server main loop

        Waits for client requests or events generated by "drbdsetup events".
        """
        gobject.MainLoop().run()


    def init_events(self):
        """
        Initialize callbacks for events generated by "drbdsetup events"

        Starts "drbdsetup events" as a child process with drbdsetup's standard
        output piped back to the drbdmanage server. A GMainLoop controlled
        callback is set up, so the drbdmanage server can react to log entries
        generated by drbdsetup.

        The callback functions are:
            drbd_event        whenever data becomes readable on the pipe
            restart_events    when the pipe needs to be reopened
        """
        # Unregister the input handler
        if self._evt_in_h is not None:
            gobject.source_remove(self._evt_in_h)
        self._evt_in_h = None

        # Unregister the hangup handler
        if self._evt_hup_h is not None:
            gobject.source_remove(self._evt_hup_h)
        self._evt_hup_h = None

        # If there is an existing events subprocess, attempt to terminate it
        # first. Subprocess termination is attempted in multiple stages:
        # 1. Attempt to close drbdmanage's receiver pipe, so the subprocess
        #    should notice that and exit, then give the subprocess a short
        #    period of time to exit
        # 2. If the subprocess is still there, send SIGTERM and give the
        #    subprocess another short period of time to exit
        # 3. ... wait a bit longer
        # 4. send SIGKILL and wait a short period of time for the subprocess
        #    to be killed
        # 5. ... wait a bit longer
        # 6. If the subprocess has ended, its remaining zombie process slot
        #    will be cleaned up, otherwise, drbdmanage stops polling for the
        #    subprocess at that point, because it is unclear whether the
        #    process will ever end. If it does, it will probably remain in the
        #    OS's process table as a zombie process.
        # The multiple stages are there to make subprocess termination as
        # reliable as possible, but as fast as reasonably possible at the same
        # time. Commonly, the subprocess should have exited after stage 1
        # or stage 2. Stage 6 should not be reached during normal operation.
        try:
            if self._proc_evt is not None:
                term_stage_end = 7
                term_stage     = 1
                while term_stage < term_stage_end:
                    if term_stage == 1:
                        # Stage 1: Close drbdmanage's receiver pipe
                        #          (that's the subprocess' stdout pipe)
                        stdout_pipe = self._proc_evt.stdout
                        if stdout_pipe is not None:
                            stdout_pipe.close()
                            time.sleep(self.EVT_TERM_SLEEP_SHORT)
                    elif term_stage >= 2:
                        # Check whether the process is still running
                        self._proc_evt.poll()
                        if self._proc_evt.returncode is None:
                            if term_stage == 2:
                                # Stage 2: Send SIGTERM and wait
                                self._term_events(self._proc_evt,
                                                  signal.SIGTERM)
                                time.sleep(self.EVT_TERM_SLEEP_SHORT)
                            elif term_stage == 3:
                                # Stage 3: wait longer
                                time.sleep(self.EVT_TERM_SLEEP_LONG)
                            elif term_stage == 4:
                                # Stage 4: send SIGKILL and wait
                                self._term_events(self._proc_evt,
                                                  signal.SIGKILL)
                                time.sleep(self.EVT_TERM_SLEEP_SHORT)
                            elif term_stage == 5:
                                # Stage 5: wait longer
                                time.sleep(self.EVT_TERM_SLEEP_LONG)
                            # Stage 6: no-op; runs through the poll() to
                            #          clean up the zombie process if the
                            #          OS killed the subprocess
                        else:
                            # If the process is not running anymore,
                            # leave the termination loop
                            term_stage = term_stage_end
                    # Enter the next stage
                    if term_stage < term_stage_end:
                        term_stage += 1
                # Forget the process handle; the variable will be reused
                # for a newly created events subprocess
                self._proc_evt = None
        except (OSError, IOError):
            pass

        # Initialize a new events subprocess
        evt_util = build_path(self.get_conf_value(self.KEY_DRBDADM_PATH),
                              self.EVT_UTIL)
        self._proc_evt = subprocess.Popen(
            [self.EVT_UTIL, "events2", "all"], 0,
            evt_util, stdout=subprocess.PIPE,
            close_fds=True
        )
        self._evt_file = self._proc_evt.stdout
        fcntl.fcntl(self._evt_file.fileno(),
                    fcntl.F_SETFL,
                    fcntl.F_GETFL | os.O_NONBLOCK)
        self._reader = NioLineReader(self._evt_file)
        # detect readable data on the pipe
        self._evt_in_h = gobject.io_add_watch(
            self._evt_file.fileno(),
            gobject.IO_IN, self.drbd_event
        )
        # detect broken pipe
        self._evt_hup_h = gobject.io_add_watch(
            self._evt_file.fileno(),
            gobject.IO_HUP, self.restart_events
        )


    def restart_events(self, evt_fd, condition):
        """
        Detects broken pipe, killed drbdsetup process, etc. and reinitialize
        the event callbacks
        """
        # unregister any existing event handlers for the events log
        log_error = True
        logging.error("drbdsetup events tracing has failed, restarting")

        retry = True
        while retry:
            try:
                self.init_events()
                retry = False
            except (OSError, IOError):
                if log_error:
                    logging.critical(
                        "cannot restart drbdsetup events tracing, "
                        "this node is inoperational"
                    )
                    logging.critical(
                        "retrying restart of drbdsetup events "
                        "tracing every 30 seconds"
                    )
                    log_error = False
                time.sleep(30)
        logging.info("drbdsetup events tracing reestablished")
        self._drbd_mgr.run(False, False)
        # Unregister this event handler, init_events has registered a new one
        # for the new events pipe
        return False


    def drbd_event(self, evt_fd, condition):
        """
        Receives log entries from the "drbdsetup events" child process

        Detect state changes by reading the drbdsetup events log. If another
        node modifies the configuration on the drbdmanage control volume,
        this becomes visible in the event log as a remote role change on the
        drbdmanage control volume. In this case, the DRBD resource manager is
        invoked to check, whether any changes are required on this node.
        """
        changed = False
        while True:
            line = self._reader.readline()
            if line is not None:
                line = line.strip()
                if self.dbg_events:
                    logging.debug("received event line: %s" % line)
                sys.stderr.flush()
                if not changed:
                    match = self._evt_pat.match(line)
                    if match is not None:
                        # try to parse args
                        # TODO: maybe this pattern can be pre-compiled, too?
                        line_data = dict(
                            re.findall('([\w-]+):(\S+)', match.group('attrs'))
                        )

                        evt_type   = match.group('type')
                        evt_source = match.group('source')

                        # Detect potential changes of the data on the
                        # control volume
                        changed = self._drbd_event_change_trigger(
                            evt_type, evt_source, line_data
                        )
            else:
                break
        if changed:
            self._drbd_mgr.run(False, False)
        # True = GMainLoop shall not unregister this event handler
        return True


    def _drbd_event_change_trigger(self, evt_type, evt_source, line_data):
        changed = False
        try:
            if (evt_type == self.EVT_TYPE_CHANGE and
                line_data[self.EVT_ARG_NAME] == DRBDCTRL_RES_NAME):
                    # Check: role change to Secondary
                    try:
                        role = line_data[self.EVT_ARG_ROLE]
                        if (evt_source == self.EVT_SRC_CON and
                            role == self.EVT_ROLE_SECONDARY):
                                changed = True
                                if self.dbg_events:
                                    logging.debug(
                                        "event change trigger role:Secondary"
                                    )
                    except KeyError:
                        # Ignore: Not a role change
                        pass

                    # Check: replication change to SyncTarget
                    try:
                        replication = line_data[self.EVT_ARG_REPL]
                        if (evt_source == self.EVT_SRC_PEERDEV and
                            replication == self.EVT_REPL_SYNCTARGET):
                                changed = True
                                if self.dbg_events:
                                    logging.debug(
                                        "event change trigger "
                                        "replication:SyncTarget"
                                    )
                    except KeyError:
                        # Ignore: Not a replication change
                        pass
        except KeyError:
            # Ignore lines with missing fields (line_data keys)
            pass
        return changed


    def _term_events(self, proc_evt, signal):
        """
        Sends signals to a subprocess
        """
        if proc_evt.pid is not None:
            os.kill(proc_evt.pid, signal)


    def init_logging(self):
        """
        Initialize global logging
        """
        self._root_logger = logging.getLogger("")
        syslog_h = logging.handlers.SysLogHandler(address="/dev/log")
        syslog_f = logging.Formatter(fmt=self.LOGGING_FORMAT)
        syslog_h.setFormatter(syslog_f)
        self._root_logger.addHandler(syslog_h)
        self._root_logger.setLevel(logging.INFO)


    def load_server_conf(self):
        """
        Loads the server configuration file

        The server configuration is loaded from the server's configuration
        file (commonly /etc/drbdmanaged.conf), and is then unified with any
        existing default values.
        Values from the configuration override default configuration values.
        Values not specified in the configuration file are inherited from
        the default configuration. Any values specified in the configuration
        file that are not known in the default configuration are discarded.
        """
        in_file = None
        try:
            in_file = open(SERVER_CONFFILE, "r")
            conffile = ConfFile(in_file)
            conf_loaded = conffile.get_conf()
            if conf_loaded is not None:
                self._conf = (
                    ConfFile.conf_defaults_merge(
                        self.CONF_DEFAULTS, conf_loaded
                    )
                )
            else:
                self._conf = self.CONF_DEFAULTS
        except IOError as ioerr:
            if ioerr.errno == errno.EACCES:
                logging.warning(
                    "cannot open configuration file '%s', permission denied"
                    % (SERVER_CONFFILE)
                )
            elif ioerr.errno != errno.ENOENT:
                logging.warning(
                    "cannot open configuration file '%s', "
                    "error returned by the OS is: %s"
                    % (SERVER_CONFFILE, ioerr.strerror)
                )
        finally:
            if self._conf is None:
                self._conf = self.CONF_DEFAULTS
            if in_file is not None:
                in_file.close()


    def get_conf_value(self, key):
        """
        Returns a configuration value.

        All configuration values are stored as strings. If another type is
        required, any function that retrieves the configuration value
        should attempt to convert the value to the required type. If that
        conversion fails, the configuration value from the default
        configuration (CONF_DEFAULTS) should be used instead.

        @param   key: the name (key) of the configuration value
        @return: configuration value
        @rtype:  str
        """
        return self._conf.get(key)


    def get_cluster_conf_value(self, key):
        """
        Retrieves a value from the replicated cluster configuration
        """
        return self._cluster_conf.get(key)


    def peek_serial(self):
        """
        Returns the current serial number without changing it

        Returns the current serial number, whether or not it is still in use
        for changes.
        """
        serial_str = self._cluster_conf.get(SERIAL)
        if serial_str is None:
            serial = 0
        else:
            try:
                serial = long(serial_str)
            except TypeError:
                # FIXME: a better solution would be to find the greatest
                #        serial number set on any object in the
                #        configuration, and then to increase that number
                #        and use it as the new serial number of the cluster
                #        configuration.
                #        Another possibility would be to reset the serial
                #        number on
                #        all objects to 0 and then set a serial of 1 here.
                #        The current workaround merely keeps the system
                #        running, but the serial numbers are totally messed
                #        up if this happens.
                logging.error(
                    "Unparseable serial number in the cluster "
                    "configuration, setting serial=0 to recover"
                )
                serial = 0
        return serial


    def get_serial(self):
        """
        Returns a serial number for configuration changes

        Upon the first call of this function in a sequence of changes, a
        new serial number is generated and returned. Upon subsequent calls,
        the same serial number is returned until the change generation is
        closed by calling close_serial().
        """
        serial = self.peek_serial()
        if self._change_open == False:
            self._change_open = True
            serial += 1
            self._cluster_conf[SERIAL] = str(serial)
        return serial


    def close_serial(self):
        """
        Closes the current generation of configuration changes

        After a generation of configuration changes has been closed,
        the next call of get_serial() will open a new change generation and
        will return a new serial number.
        """
        self._change_open = False


    def get_drbd_mgr(self):
        return self._drbd_mgr


    def get_bd_mgr(self):
        return self._bd_mgr


    def iterate_nodes(self):
        """
        Returns an iterator over all registered nodes
        """
        return self._nodes.itervalues()


    def iterate_resources(self):
        """
        Returns an iterator over all registered resources
        """
        return self._resources.itervalues()


    def get_node(self, name):
        """
        Retrieves a node by its name

        @return: the named node object or None if no object with the specified
                 name exists
        """
        node = None
        try:
            node = self._nodes.get(name)
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            # FIXME: where does that come from? This function is supposed
            #        to return a node or no node, but not something else
            return DM_DEBUG
        return node


    def get_resource(self, name):
        """
        Retrieves a resource by its name

        @return: the named resource object or None if no object with the
                 specified name exists
        """
        resource = None
        try:
            resource = self._resources.get(name)
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
        return resource


    def get_volume(self, name, vol_id):
        """
        Retrieves a volume by its name

        @return: the volume object specified by the name of the resource it is
                 contained in and by its volume id or None if no object with
                 the specified name exists
        """
        volume = None
        try:
            resource = self._resources.get(name)
            if resource is not None:
                volume = resource.get_volume(vol_id)
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
        return volume


    # Get the node this server is running on
    def get_instance_node(self):
        """
        Retrieves the node that represents the host this instance of
        drbdmanage is currently running on.

        @return: the node object this instance of drbdmanage is running on
                 or None if no node object is registered for this host
        """
        node = None
        try:
            node = self._nodes[self._instance_node_name]
        except KeyError:
            pass
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
        return node


    # Get the name of the node this server is running on
    def get_instance_node_name(self):
        """
        Returns the name used by the drbdmanage server to look for a node
        object that represents the hosts this drbdmanage server is currently
        running on

        @return: name of the node object this drbdmanage server is running on
        """
        return self._instance_node_name


    def _cluster_nodes_update(self):
        """
        Flags other nodes for reconfiguration of the control volume
        """
        inst_node = self.get_instance_node()
        for peer_node in self._nodes.itervalues():
            if peer_node != inst_node:
                peer_node.set_state(peer_node.get_state() |
                                    DrbdNode.FLAG_UPDATE)


    def poke(self):
        """
        Causes cluster nodes to perform pending actions by changing the serial

        Changes the serial number, thereby changing the hash value of the
        cluster configuration and causing all connected nodes to perform
        pending actions

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc   = []
        try:
            # Run the DrbdManager, overriding the hash check and changing
            # the serial number to cause all cluster nodes to run
            # any scheduled changes
            self._drbd_mgr.run(True, True)
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def create_node(self, node_name, props):
        """
        Registers a DRBD cluster node

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc   = []
        persist = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                sub_rc = self._create_node(False, node_name, props, None, None)
                if sub_rc == DM_SUCCESS or sub_rc == DM_ECTRLVOL:
                    self.save_conf_data(persist)
                else:
                    add_rc_entry(fn_rc, sub_rc, dm_exc_text(sub_rc))
            else:
                raise PersistenceException
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def _create_node(self, initial, node_name, props, bdev, port):
        """
        Register DRBD cluster nodes and update control volume configuration

        Used by create_node() and init_node()
        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc   = DM_EPERSIST
        node    = None
        try:
            if self._nodes.get(node_name) is not None:
                fn_rc = DM_EEXIST
            else:
                addr    = None
                addrfam = DrbdNode.AF_IPV4
                try:
                    addr     = props[NODE_ADDR]
                except KeyError:
                    pass
                try:
                    af_label = props[NODE_AF]
                    if af_label == DrbdNode.AF_IPV4_LABEL:
                        addrfam = DrbdNode.AF_IPV4
                    elif af_label == DrbdNode.AF_IPV6_LABEL:
                        addrfam = DrbdNode.AF_IPV6
                except KeyError:
                    pass
                try:
                    if addr is not None and addrfam is not None:
                        node_id = self.get_free_drbdctrl_node_id()
                        if node_id != -1:
                            node = DrbdNode(
                                node_name, addr, addrfam, node_id,
                                0, -1, -1,
                                self.get_serial, None, None
                            )
                            # Merge only auxiliary properties into the
                            # DrbdNode's properties container
                            aux_props = aux_props_selector(props)
                            node.get_props().merge_gen(aux_props)
                            self._nodes[node.get_name()] = node
                            self._cluster_nodes_update()
                            # create or update the drbdctrl.res file
                            check_configure = self._configure_drbdctrl(
                                initial,
                                None, bdev, port
                            )
                            if check_configure == 0:
                                self._drbd_mgr.adjust_drbdctrl()
                                fn_rc = DM_SUCCESS
                            else:
                                fn_rc = DM_ECTRLVOL
                        else:
                            fn_rc = DM_ENODEID
                    else:
                        fn_rc = DM_EINVAL
                except InvalidNameException:
                    fn_rc = DM_ENAME
        except PersistenceException as pexc:
            raise pexc
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        return fn_rc


    def remove_node(self, node_name, force):
        """
        Marks a node for removal from the DRBD cluster
        * Orders the node to undeploy all volumes
        * Orders all other nodes to disconnect from the node

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc   = []
        errors  = False
        persist = None
        node    = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                node = self._nodes[node_name]
                if (not force) and node.has_assignments():
                    drbdctrl_flag = False
                    for assignment in node.iterate_assignments():
                        assignment.undeploy()
                        resource = assignment.get_resource()
                        for peer_assg in resource.iterate_assignments():
                            peer_assg.update_connections()
                    node.remove()
                    self._drbd_mgr.perform_changes()
                else:
                    drbdctrl_flag = True
                    # drop all associated assignments
                    for assignment in node.iterate_assignments():
                        resource = assignment.get_resource()
                        resource.remove_assignment(assignment)
                        # tell the remaining nodes that have this resource to
                        # drop the connection to the deleted node
                        for peer_assg in resource.iterate_assignments():
                            peer_assg.update_connections()
                    del self._nodes[node_name]
                    self._cluster_nodes_update()
                self.save_conf_data(persist)
                if drbdctrl_flag:
                    self.reconfigure_drbdctrl()
            else:
                raise PersistenceException
        except KeyError:
            add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT),
                         [ NODE_NAME, node_name ])
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def create_resource(self, res_name, props):
        """
        Registers a new resource that can be deployed to DRBD cluster nodes

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = []
        errors   = False
        resource = None
        persist  = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                resource = self._resources.get(res_name)
                if resource is not None:
                    add_rc_entry(fn_rc, DM_EEXIST, dm_exc_text(DM_EEXIST),
                                 [ RES_NAME, resource.get_name() ])
                else:
                    port = RES_PORT_NR_AUTO
                    secret = generate_secret()
                    if secret is not None:
                        try:
                            port = int(props[RES_PORT])
                        except KeyError:
                            pass
                        if port == RES_PORT_NR_AUTO:
                            port = self.get_free_port_nr()
                        if port < 1 or port > 65535:
                            add_rc_entry(fn_rc, DM_EPORT, dm_exc_text(DM_EPORT),
                                         [ RES_PORT, str(port) ])
                        else:
                            resource = DrbdResource(
                                res_name,
                                port, secret, 0, None,
                                self.get_serial, None, None
                            )
                            # Merge only auxiliary properties into the
                            # DrbdResource's properties container
                            aux_props = aux_props_selector(props)
                            resource.get_props().merge_gen(aux_props)
                            self._resources[resource.get_name()] = resource
                            self.save_conf_data(persist)
                            add_rc_entry(fn_rc, DM_SUCCESS,
                                         dm_exc_text(DM_SUCCESS))
                    else:
                        add_rc_entry(fn_rc, DM_ESECRETG,
                                     dm_exc_text(DM_ESECRETG))
            else:
                raise PersistenceException
        except ValueError:
            add_rc_entry(fn_rc, DM_EINVAL, dm_exc_text(DM_EINVAL),
                [ RES_PORT, port ])
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except InvalidNameException:
            add_rc_entry(fn_rc, DM_ENAME, dm_exc_text(DM_ENAME),
                [ RES_NAME, res_name ])
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def modify_resource(self, res_name, serial, props):
        """
        Modifies resource properties

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = DM_EPERSIST
        resource = None
        persist  = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                resource = self._resources.get(res_name)
                if resource is None:
                    fn_rc = DM_ENOENT
                else:
                    port_nr = None
                    for keyval in props.iteritems():
                        key = keyval[0]
                        val = keyval[1]
                        if key == RES_PORT:
                            try:
                                port_nr = int(val)
                            except ValueError:
                                fn_rc = DM_EINVAL
                        else:
                            fn_rc = DM_EINVAL
                        # TODO: port change - not implemented
                        aux_props = aux_props_selector(props)
                        resource.get_props().merge_gen(aux_props)
                        self._resources[resource.get_name()] = resource
                        self.save_conf_data(persist)
                        fn_rc = DM_SUCCESS
        except PersistenceException:
            pass
        except InvalidNameException:
            fn_rc = DM_ENAME
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        finally:
            self.end_modify_conf(persist)
        return fn_rc


    def modify_volume(self, res_name, vol_id, serial, props):
        """
        Modifies volume properties

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = []
        add_rc_entry(fn_rc, DM_ENOTIMPL, dm_exc_text(DM_ENOTIMPL))
        return fn_rc


    def resize_volume(self, res_name, vol_id, serial, size_kiB, delta_kiB):
        """
        Resizes a volume

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = []
        add_rc_entry(fn_rc, DM_ENOTIMPL, dm_exc_text(DM_ENOTIMPL))
        return fn_rc


    def remove_resource(self, res_name, force):
        """
        Marks a resource for removal from the DRBD cluster
        * Orders all nodes to undeploy all volume of this resource

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = []
        persist  = None
        resource = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                resource = self._resources[res_name]
                if (not force) and resource.has_assignments():
                    for assg in resource.iterate_assignments():
                        assg.undeploy()
                    resource.remove()
                    self._drbd_mgr.perform_changes()
                else:
                    for assg in resource.iterate_assignments():
                        node = assg.get_node()
                        node.remove_assignment(assg)
                    del self._resources[resource.get_name()]
                self.save_conf_data(persist)
            else:
                raise PersistenceException
        except KeyError:
            add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def create_volume(self, res_name, size_kiB, props):
        """
        Adds a volume to a resource

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc   = []
        volume  = None
        persist = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                resource = self._resources.get(res_name)
                if resource is None:
                    add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
                else:
                    minor = MinorNr.MINOR_NR_AUTO
                    try:
                        minor = int(props[VOL_MINOR])
                    except KeyError:
                        pass
                    except ValueError:
                        raise InvalidMinorNrException
                    if minor == MinorNr.MINOR_NR_AUTO:
                        minor = self.get_free_minor_nr()
                    if minor == MinorNr.MINOR_NR_ERROR:
                        raise InvalidMinorNrException
                    vol_id = self.get_free_volume_id(resource)
                    if vol_id == -1:
                        add_rc_entry(fn_rc, DM_EVOLID, dm_exc_text(DM_EVOLID))
                    else:
                        chg_serial = self.get_serial()
                        volume = DrbdVolume(vol_id, size_kiB, MinorNr(minor),
                                            0, self.get_serial, None, None)
                        # Merge only auxiliary properties into the
                        # DrbdVolume's properties container
                        aux_props = aux_props_selector(props)
                        volume.get_props().merge_gen(aux_props)
                        resource.add_volume(volume)
                        for assg in resource.iterate_assignments():
                            assg.update_volume_states(chg_serial)
                            vol_st = assg.get_volume_state(volume.get_id())
                            if vol_st is not None:
                                vol_st.deploy()
                                vol_st.attach()
                        self._drbd_mgr.perform_changes()
                        self.save_conf_data(persist)
            else:
                raise PersistenceException
        except InvalidNameException:
            add_rc_entry(fn_rc, DM_ENAME, dm_exc_text(DM_ENAME))
        except InvalidMinorNrException:
            add_rc_entry(fn_rc, DM_EMINOR, dm_exc_text(DM_EMINOR))
        except VolSizeRangeException:
            add_rc_entry(fn_rc, DM_EVOLSZ, dm_exc_text(DM_EVOLSZ))
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def remove_volume(self, res_name, vol_id, force):
        """
        Marks a volume for removal from the DRBD cluster
        * Orders all nodes to undeploy the volume

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc   = []
        persist = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                resource = self._resources[res_name]
                volume   = resource.get_volume(vol_id)
                if volume is None:
                    raise KeyError
                else:
                    if (not force) and resource.has_assignments():
                        for assg in resource.iterate_assignments():
                            peer_vol_st = assg.get_volume_state(vol_id)
                            if peer_vol_st is not None:
                                peer_vol_st.undeploy()
                        volume.remove()
                        self._drbd_mgr.perform_changes()
                    else:
                        resource.remove_volume(vol_id)
                        for assg in resource.iterate_assignments():
                            assg.remove_volume_state(vol_id)
                    self.save_conf_data(persist)
            else:
                raise PersistenceException
        except KeyError:
            add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def assign(self, node_name, res_name, props):
        """
        Assigns a resource to a node
        * Orders all participating nodes to deploy all volumes of
          resource

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc   = []
        persist = None
        try:
            tstate = Assignment.FLAG_DEPLOY
            cstate = 0

            # Set flags from props
            flag_overwrite = False
            flag_diskless  = False
            flag_connect   = True
            flag_discard   = False
            try:
                flag_overwrite = string_to_bool(props[FLAG_OVERWRITE])
            except (KeyError, TypeError):
                pass
            try:
                flag_diskless  = string_to_bool(props[FLAG_DISKLESS])
            except (KeyError, TypeError):
                pass
            try:
                flag_connect   = string_to_bool(props[FLAG_CONNECT])
            except (KeyError, TypeError):
                pass
            try:
                flag_discard   = string_to_bool(props[FLAG_DISCARD])
            except (KeyError, TypeError):
                pass

            persist = self.begin_modify_conf()
            if persist is not None:
                node     = self._nodes.get(node_name)
                resource = self._resources.get(res_name)
                if node is None or resource is None:
                    add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
                else:
                    assignment = node.get_assignment(resource.get_name())
                    if assignment is not None:
                        add_rc_entry(fn_rc, DM_EEXIST, dm_exc_text(DM_EEXIST))
                    else:
                        # check conflicting flags
                        if (flag_overwrite and flag_diskless):
                            add_rc_entry(
                                fn_rc, DM_EINVAL, dm_exc_text(DM_EINVAL)
                            )
                        elif (flag_overwrite and flag_discard):
                            add_rc_entry(
                                fn_rc, DM_EINVAL, dm_exc_text(DM_EINVAL)
                            )
                        else:
                            # If the overwrite flag is set on this
                            # assignment, turn it off on all the assignments
                            # to other nodes
                            if flag_overwrite:
                                for assg in resource.iterate_assignments():
                                    assg.clear_tstate_flags(
                                        Assignment.FLAG_OVERWRITE
                                    )
                            tstate = (
                                tstate |
                                (Assignment.FLAG_OVERWRITE if flag_overwrite
                                    else 0) |
                                (Assignment.FLAG_DISCARD   if flag_discard
                                    else 0) |
                                (Assignment.FLAG_CONNECT   if flag_connect
                                    else 0) |
                                (Assignment.FLAG_DISKLESS  if flag_diskless
                                    else 0)
                            )
                            assign_rc = (
                                self._assign(node, resource, cstate, tstate)
                            )
                            if assign_rc == DM_SUCCESS:
                                assignment = node.get_assignment(
                                    resource.get_name()
                                )
                                aux_props = aux_props_selector(props)
                                assignment.get_props().merge_gen(aux_props)
                                self._drbd_mgr.perform_changes()
                                self.save_conf_data(persist)
                            else:
                                add_rc_entry(
                                    fn_rc, assign_rc, dm_exc_text(assign_rc)
                                )
            else:
                raise PersistenceException
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except ValueError:
            add_rc_entry(fn_rc, DM_EINVAL, dm_exc_text(DM_EINVAL))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def unassign(self, node_name, res_name, force):
        """
        Removes the assignment of a resource to a node
        * Orders the node to undeploy all volumes of the resource

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc   = []
        persist = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                try:
                    node = self._nodes[node_name]
                except KeyError:
                    add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT),
                                 [ NODE_NAME, node_name ])
                try:
                    resource = self._resources[res_name]
                except KeyError:
                    add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT),
                                 [ RES_NAME, res_name ])
                if node is None or resource is None:
                    add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
                else:
                    assignment = node.get_assignment(resource.get_name())
                    if assignment is None:
                        add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
                    else:
                        sub_rc = self._unassign(assignment, force)
                        if sub_rc == DM_SUCCESS:
                            self._drbd_mgr.perform_changes()
                            self.save_conf_data(persist)
                        else:
                            add_rc_entry(fn_rc, sub_rc, dm_exc_text(sub_rc))
            else:
                raise PersistenceException
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def _assign(self, node, resource, cstate, tstate):
        """
        Implementation - see assign()

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = DM_DEBUG
        try:
            serial = self.get_serial()
            node_id = self.get_free_node_id(resource)
            if node_id == -1:
                # no free node ids
                fn_rc = DM_ENODEID
            else:
                # The block device is set upon allocation of the backend
                # storage area on the target node
                assignment = Assignment(node, resource, node_id,
                                        cstate, tstate, 0, None,
                                        self.get_serial, None, None)
                for vol_state in assignment.iterate_volume_states():
                    vol_state.deploy()
                    if tstate & Assignment.FLAG_DISKLESS == 0:
                        vol_state.attach()
                node.add_assignment(assignment)
                resource.add_assignment(assignment)
                for assignment in resource.iterate_assignments():
                    if assignment.is_deployed():
                        assignment.update_connections()
                fn_rc = DM_SUCCESS
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
        return fn_rc


    def _unassign(self, assignment, force):
        """
        Implementation - see unassign()

        @return: standard return code defined in drbdmanage.exceptions
        """
        try:
            serial   = self.get_serial()
            node     = assignment.get_node()
            resource = assignment.get_resource()
            if (not force) and assignment.is_deployed():
                assignment.disconnect()
                assignment.undeploy()
            else:
                assignment.remove()
            for assignment in resource.iterate_assignments():
                if (assignment.get_node() != node and
                    assignment.is_deployed()):
                        assignment.update_connections()
            self.cleanup()
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            return DM_DEBUG
        return DM_SUCCESS


    def cluster_free_query(self, redundancy):
        """
        Determines the maximum size of an n-times redundantly deployed volume
        """
        fn_rc = []
        # Default of 0 if the free space is unknown
        # TODO: There should be a distinction between the two cases:
        #       1) there is no free space
        #       2) there are too few nodes that have a known poolfree size
        #          to determine whether there is any free space
        free_space = 0
        try:
            if redundancy >= 1:
                if redundancy <= len(self._nodes):
                    # Select nodes where the amount of free space on
                    # that node is known
                    selected = []
                    for node in self._nodes.itervalues():
                        poolfree = node.get_poolfree()
                        if poolfree != -1:
                            selected.append(node)

                    # Sort by free space
                    selected = sorted(
                        selected,
                        key=lambda node: node.get_poolfree(), reverse=True
                    )
                    if len(selected) >= redundancy:
                        node = selected[redundancy - 1]
                        gross_free = node.get_poolfree()
                        max_peers = self.DEFAULT_MAX_PEERS
                        try:
                            max_peers = int(
                                self.get_conf_value(self.KEY_MAX_PEERS)
                            )
                        except ValueError:
                            # Unparseable configuration value;
                            # no-op: keep default value
                            pass
                        free_space = MetaData.get_net_data_kiB(
                            gross_free, max_peers
                        )
                else:
                    # requested redundancy exceeds the
                    # number of nodes in the cluster
                    add_rc_entry(fn_rc, DM_ENODECNT, dm_exc_text(DM_ENODECNT))
            else:
                # requested a redundancy of less than 1, which is an invalid
                # number of nodes
                add_rc_entry(fn_rc, DM_EINVAL, dm_exc_text(DM_EINVAL))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc, free_space


    def auto_deploy(self, res_name, count, delta, site_clients):
        """
        Deploys a resource to a number of nodes

        The selected resource is deployed to a number of nodes, either by
        initially deploying the resource, or by deploying the resource
        on additional nodes or undeploying the resource from nodes where it
        is currently deployed, until the number of nodes where the resource
        is deployed either:
            - matches count, if the supplied count value is non-zero
        or
            - has been changed by delta, if the supplied delta value
              is non-zero
        If both supplied values are non-zero, then the operation is aborted
        due to potentially conflicting information, and DM_EINVAL is added to
        the list of return codes.

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc   = []
        persist = None
        try:
            if ((count != 0 and delta != 0) or count < 0):
                add_rc_entry(fn_rc, DM_EINVAL, dm_exc_text(DM_EINVAL))
            else:
                deployer = plugin_import(
                  self.get_conf_value(self.KEY_DEPLOYER_NAME))
                if deployer is None:
                    raise PluginException

                persist  = self.begin_modify_conf()
                if persist is None:
                    raise PersistenceException

                maxnodes = self.DEFAULT_MAX_NODE_ID
                try:
                    maxnodes = int(self._conf[self.KEY_MAX_NODE_ID]) + 1
                except ValueError:
                    pass
                crtnodes = len(self._nodes)
                maxcount = maxnodes if maxnodes < crtnodes else crtnodes
                resource = self._resources[res_name]
                assigned_count = resource.assigned_count()

                # Calculate target node count
                if delta != 0:
                    final_count = assigned_count + delta
                    if final_count < 1:
                        add_rc_entry(fn_rc, DM_EINVAL, dm_exc_text(DM_EINVAL))
                        return fn_rc
                else:
                    final_count = count


                # Try to achieve it
                if final_count > maxcount:
                    add_rc_entry(fn_rc, DM_ENODECNT, dm_exc_text(DM_ENODECNT))

                elif final_count <= 0:
                    add_rc_entry(fn_rc, DM_EINVAL, dm_exc_text(DM_EINVAL))

                elif final_count > assigned_count:
                    # ========================================
                    # DEPLOY / EXTEND
                    # ========================================
                    # FIXME: extend does nothing for some unknown reason,
                    #        but succeeds (exit code = 0)
                    """
                    calculate the amount of memory required to deploy all
                    volumes of the resource
                    """
                    size_sum = 0
                    max_peers = self.DEFAULT_MAX_PEERS
                    try:
                        max_peers = int(
                            self.get_conf_value(self.KEY_MAX_PEERS)
                        )
                    except ValueError:
                        # Unparseable configuration entry;
                        # no-op: use default value instead
                        pass
                    for vol in resource.iterate_volumes():
                        # Calculate required gross space for a volume
                        # with the specified net space
                        size_sum += MetaData.get_gross_data_kiB(
                            vol.get_size_kiB(), max_peers
                        )
                    """
                    filter nodes that do not have the resource deployed yet
                    """
                    undeployed = {}
                    for node in self._nodes.itervalues():
                        # skip nodes, where:
                        #   - resource is deployed already
                        #   - resource is being deployed
                        #   - resource is being undeployed
                        if (resource.get_assignment(node.get_name()) is None):
                            undeployed[node.get_name()] = node
                    """
                    Call the deployer plugin to select nodes for deploying
                    the resource
                    """
                    diff = final_count - assigned_count
                    selected = []
                    sub_rc = deployer.deploy_select(
                        undeployed, selected,
                        diff, size_sum, True
                    )
                    if sub_rc == DM_SUCCESS:
                        for node in selected:
                            self._assign(
                                node, resource,
                                0,
                                Assignment.FLAG_DEPLOY |
                                Assignment.FLAG_CONNECT
                            )
                        self._drbd_mgr.perform_changes()
                        self.save_conf_data(persist)
                    else:
                        add_rc_entry(fn_rc, sub_rc, dm_exc_text(sub_rc))

                elif final_count < assigned_count:
                    # ========================================
                    # REDUCE
                    # ========================================
                    ctr = assigned_count
                    # If there are assignments that are waiting for
                    # deployment, but do not have the resource deployed
                    # yet, undeploy those first
                    if ctr > final_count:
                        for assg in resource.iterate_assignments():
                            if ((assg.get_tstate() &
                                Assignment.FLAG_DEPLOY != 0) and
                                (assg.get_cstate() &
                                Assignment.FLAG_DEPLOY == 0)):
                                    assg.undeploy()
                                    ctr -= 1
                            if not ctr > final_count:
                                break
                    if ctr > final_count:
                        # Undeploy from nodes that have the
                        # resource deployed
                        # Collect nodes where the resource is deployed
                        deployed = {}
                        for assg in resource.iterate_assignments():
                            if ((assg.get_tstate() &
                                Assignment.FLAG_DEPLOY != 0) and
                                (assg.get_cstate() &
                                Assignment.FLAG_DEPLOY != 0)):
                                    node = assg.get_node()
                                    deployed[node.get_name()] = node
                        """
                        Call the deployer plugin to select nodes for
                        undeployment of the resource
                        """
                        diff = ctr - final_count
                        selected = []
                        deployer.undeploy_select(
                            deployed, selected,
                            diff, True
                        )
                        for node in selected:
                            assg = node.get_assignment(resource.get_name())
                            if site_clients:
                                # turn the node into a client
                                assg.deploy_client()
                            else:
                                self._unassign(assg, False)
                    self._drbd_mgr.perform_changes()
                    self.save_conf_data(persist)

            # condition (final_count == assigned_count) is successful, too

            if site_clients:
                # turn all remaining nodes into clients
                self._site_clients(resource, None)
        except KeyError:
            add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except PluginException:
            add_rc_entry(fn_rc, DM_EPLUGIN, dm_exc_text(DM_EPLUGIN))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def auto_undeploy(self, res_name, force):
        """
        Undeploys a resource from all nodes

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc   = []
        persist = None
        try:
            persist = self.begin_modify_conf()
            if persist is None:
                raise PersistenceException

            resource = self._resources[res_name]
            removable = []
            for assg in resource.iterate_assignments():
                if (not force) and assg.is_deployed():
                    assg.disconnect()
                    assg.undeploy()
                else:
                    removable.append(assg)
            for assg in removable:
                assg.remove()
            self._drbd_mgr.perform_changes()
            self.save_conf_data(persist)
        except KeyError:
            add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def _site_clients(self, resource, site):
        """
        Turn all nodes that do replicate a resource into clients
        """
        for node in self._nodes.itervalues():
            assg = node.get_assignment(resource.get_name())
            if assg is None:
                self._assign(
                    node, resource,
                    0,
                    Assignment.FLAG_DEPLOY | Assignment.FLAG_CONNECT |
                    Assignment.FLAG_DISKLESS
                )
            else:
                tstate = assg.get_tstate()
                if (tstate & Assignment.FLAG_DEPLOY) == 0:
                    assg.deploy_client()


    def modify_state(self, node_name, res_name,
      cstate_clear_mask, cstate_set_mask, tstate_clear_mask, tstate_set_mask):
        """
        Modifies the tstate (target state) of an assignment

        @return: standard return code defined in drbdmanage.exceptions
        """
        # FIXME: function is now modify_assignment, new signature
        fn_rc   = []
        persist = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                node = self._nodes.get(node_name)
                if node is None:
                    add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
                else:
                    assg = node.get_assignment(res_name)
                    if assg is None:
                        add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
                    else:
                        # OVERWRITE overrides DISCARD
                        if (tstate_set_mask & Assignment.FLAG_OVERWRITE) != 0:
                            tstate_clear_mask |= Assignment.FLAG_DISCARD
                            tstate_set_mask = (
                                (tstate_set_mask | Assignment.FLAG_DISCARD) ^
                                Assignment.FLAG_DISCARD
                            )
                        elif (tstate_set_mask & Assignment.FLAG_DISCARD) != 0:
                            tstate_clear_mask |= Assignment.FLAG_OVERWRITE
                        assg.clear_cstate_flags(cstate_clear_mask)
                        assg.set_cstate_flags(cstate_set_mask)
                        assg.clear_tstate_flags(tstate_clear_mask)
                        assg.set_tstate_flags(tstate_set_mask)
                        # Upon setting the OVERWRITE flag on this assignment,
                        # clear it on all other assignments
                        if (tstate_set_mask & Assignment.FLAG_OVERWRITE) != 0:
                            resource = assg.get_resource()
                            for peer_assg in resource.iterate_assignments():
                                if peer_assg != assg:
                                    peer_assg.clear_tstate_flags(
                                        Assignment.FLAG_OVERWRITE
                                    )
                        self._drbd_mgr.perform_changes()
                        self.save_conf_data(persist)
            else:
                raise PersistenceException
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    # TODO: should possibly specify connections between specific nodes
    def connect(self, node_name, res_name, reconnect):
        """
        Sets the CONNECT or RECONNECT flag on a resource's target state

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc    = []
        node     = None
        resource = None
        persist  = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                node     = self._nodes.get(node_name)
                resource = self._resources.get(res_name)
                if node is None or resource is None:
                    add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
                else:
                    assignment = node.get_assignment(resource.get_name())
                    if assignment is None:
                        add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
                    else:
                        if reconnect:
                            assignment.reconnect()
                        else:
                            assignment.connect()
                        self._drbd_mgr.perform_changes()
                        self.save_conf_data(persist)
            else:
                raise PersistenceException
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def disconnect(self, node_name, res_name, force):
        """
        Clears the CONNECT flag on a resource's target state

        @return: standard return code defined in drbdmanage.exceptions
        """
        # FIXME: what does 'force' do?
        fn_rc    = []
        node     = None
        resource = None
        persist  = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                node     = self._nodes.get(node_name)
                resource = self._resources.get(res_name)
                if node is None or resource is None:
                    add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
                else:
                    assignment = node.get_assignment(resource.get_name())
                    if assignment is None:
                        add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
                    else:
                        assignment.disconnect()
                        self._drbd_mgr.perform_changes()
                        self.save_conf_data(persist)
            else:
                raise PersistenceException
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def attach(self, node_name, res_name, vol_id):
        """
        Sets the ATTACH flag on a volume's target state

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc     = []
        node      = None
        resource  = None
        vol_state = None
        persist   = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                node     = self._nodes.get(node_name)
                resource = self._resources.get(res_name)
                if node is None or resource is None:
                    add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
                else:
                    assignment = node.get_assignment(resource.get_name())
                    if assignment is None:
                        add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
                    else:
                        vol_state = assignment.get_volume_state(vol_id)
                        if vol_state is None:
                            add_rc_entry(fn_rc, DM_ENOENT,
                                         dm_exc_text(DM_ENOENT))
                        else:
                            vol_state.attach()
                            self._drbd_mgr.perform_changes()
                            self.save_conf_data(persist)
                            add_rc_entry(fn_rc, DM_SUCCESS,
                                         dm_exc_text(DM_SUCCESS))
        except PersistenceException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc ) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def detach(self, node_name, res_name, vol_id):
        """
        Clears the ATTACH flag on a volume's target state

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc     = []
        node      = None
        resource  = None
        vol_state = None
        persist   = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                node     = self._nodes.get(node_name)
                resource = self._resources.get(res_name)
                if node is None or resource is None:
                    add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
                else:
                    assignment = node.get_assignment(resource.get_name())
                    if assignment is None:
                        add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
                    else:
                        vol_state = assignment.get_volume_state(vol_id)
                        if vol_state is None:
                            add_rc_entry(fn_rc, DM_ENOENT,
                                         dm_exc_text(DM_ENOENT))
                        else:
                            vol_state.detach()
                            self._drbd_mgr.perform_changes()
                            self.save_conf_data(persist)
            else:
                raise PersistenceException
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def update_pool(self, node_names):
        """
        Updates information about the current node's storage pool

        @return: standard return code defined in drbdmanage.exceptions
        free space
        """
        fn_rc = []
        persist = None
        try:
            persist = self.begin_modify_conf()
            if persist is not None:
                logging.info("updating storage pool information")
                sub_rc = self.update_pool_data()
                if sub_rc == DM_SUCCESS:
                    self.cleanup()
                    self.save_conf_data(persist)
                else:
                    add_rc_entry(fn_rc, sub_rc, dm_exc_text(sub_rc))
            else:
                raise PersistenceException
        except PersistenceException:
            logging.error("cannot save updated storage pool information")
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def update_pool_data(self):
        """
        Updates information about the current node's storage pool
        free space

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = DM_ESTORAGE
        try:
            inst_node = self.get_instance_node()
            if inst_node is not None:
                (stor_rc, poolsize, poolfree) = (
                    self._bd_mgr.update_pool(inst_node)
                )
                if stor_rc == DM_SUCCESS:
                    max_peers = self.DEFAULT_MAX_PEERS
                    try:
                        max_peers = int(
                            self.get_conf_value(self.KEY_MAX_PEERS)
                        )
                    except ValueError:
                        # Unparseable configuration value;
                        # no-op: keep default value
                        pass
                    size_sum = 0
                    for assignment in inst_node.iterate_assignments():
                        size_sum += assignment.get_gross_size_kiB_correction(
                            peers
                        )
                    poolfree -= size_sum
                    # If something is seriously wrong with the storage sizes,
                    # (e.g. more storage required for deploying all resources
                    #  than there is available), the pool is considered full
                    if poolfree < 0:
                        poolfree = 0
                    inst_node.set_pool(poolsize, poolfree)
                fn_rc = DM_SUCCESS
            else:
                fn_rc = DM_ENOENT
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        return fn_rc


    def cleanup(self):
        """
        Removes entries of undeployed nodes, resources, volumes or their
        supporting data structures (volume state and assignment entries)

        @return: standard return code defined in drbdmanage.exceptions
        """
        try:
            removable = []

            # delete assignments that have been undeployed
            for node in self._nodes.itervalues():
                for assignment in node.iterate_assignments():
                    tstate = assignment.get_tstate()
                    cstate = assignment.get_cstate()
                    if ((cstate & Assignment.FLAG_DEPLOY) == 0 and
                        (tstate & Assignment.FLAG_DEPLOY) == 0):
                            removable.append(assignment)
            for assignment in removable:
                assignment.remove()

            # delete nodes that are marked for removal and that do not
            # have assignments anymore
            removable = []
            for node in self._nodes.itervalues():
                nodestate = node.get_state()
                if (nodestate & DrbdNode.FLAG_REMOVE) != 0:
                    if not node.has_assignments():
                        removable.append(node)
            for node in removable:
                del self._nodes[node.get_name()]
            # if nodes have been removed, reconfigure the control volume
            if len(removable) > 0:
                try:
                    self._configure_drbdctrl(False, None, None, None)
                    self._drbd_mgr.adjust_drbdctrl()
                except (IOError, OSError) as reconf_err:
                    logging.error(
                        "Cannot reconfigure the control volume, "
                        "error description is: %s"
                        % str(reconf_err)
                    )
                self._cluster_nodes_update()

            # delete volume assignments that are marked for removal
            # and that have been undeployed
            for resource in self._resources.itervalues():
                for assg in resource.iterate_assignments():
                    removable = []
                    for vol_state in assg.iterate_volume_states():
                        vol_cstate = vol_state.get_cstate()
                        vol_tstate = vol_state.get_tstate()
                        if ((vol_cstate & DrbdVolumeState.FLAG_DEPLOY == 0) and
                            (vol_tstate & DrbdVolumeState.FLAG_DEPLOY == 0)):
                                removable.append(vol_state)
                    for vol_state in removable:
                        assg.remove_volume_state(vol_state.get_id())

            # delete volumes that are marked for removal and that are not
            # deployed on any node
            for resource in self._resources.itervalues():
                volumes = {}
                # collect volumes marked for removal
                for volume in resource.iterate_volumes():
                    if (volume.get_state() & DrbdVolume.FLAG_REMOVE) != 0:
                        volumes[volume.get_id()] = volume
                for assg in resource.iterate_assignments():
                    removable = []
                    for vol_state in assg.iterate_volume_states():
                        volume = volumes.get(vol_state.get_id())
                        if volume is not None:
                            if ((vol_state.get_cstate() &
                                DrbdVolumeState.FLAG_DEPLOY) != 0):
                                    # delete the volume from the removal list
                                    del volumes[vol_state.get_id()]
                            else:
                                removable.append(vol_state)
                        for vol_state in removable:
                            assg.remove_volume_state(vol_state.get_id())
                for vol_id in volumes.iterkeys():
                    resource.remove_volume(vol_id)

            # delete resources that are marked for removal and that do not
            # have assignments any more
            removable = []
            for resource in self._resources.itervalues():
                res_state = resource.get_state()
                if (res_state & DrbdResource.FLAG_REMOVE) != 0:
                    if not resource.has_assignments():
                        removable.append(resource)
            for resource in removable:
                del self._resources[resource.get_name()]
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            return DM_DEBUG
        return DM_SUCCESS


    def list_nodes(self, node_names, serial, filter_props, req_props):
        """
        Generates a list of node views suitable for serialized transfer

        Used by the drbdmanage client to display the node list
        """
        fn_rc = []

        def node_filter():
            for node_name in node_names:
                node = self._nodes.get(node_name)
                if node is None:
                    add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT),
                                 [ NODE_NAME, node_name ])
                else:
                    yield node

        try:
            node_list = []
            if node_names is not None and len(node_names) > 0:
                selected_nodes = node_filter()
            else:
                selected_nodes = self._nodes.itervalues()
            if serial > 0:
                selected_nodes = serial_filter(serial, selected_nodes)

            if filter_props is not None and len(filter_props) > 0:
                selected_nodes = props_filter(selected_nodes, filter_props)

            for node in selected_nodes:
                node_entry = [
                    node.get_name(),
                    node.get_properties(req_props)
                ]
                node_list.append(node_entry)
                add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
            return fn_rc, node_list
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)

        return fn_rc, None


    def list_resources(self, res_names, serial, filter_props, req_props):
        """
        Generates a list of resources views suitable for serialized transfer

        Used by the drbdmanage client to display the resources/volumes list
        """
        fn_rc = []

        def resource_filter(res_names):
            for res_name in res_names:
                res = self._resources.get(res_name)
                if res is None:
                    add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT),
                                 [ RES_NAME, res_name ])
                else:
                    yield res

        try:
            res_list = []
            if res_names is not None and len(res_names) > 0:
                selected_res = resource_filter(res_names)
            else:
                selected_res = self._resources.itervalues()
            if serial > 0:
                selected_res = serial_filter(serial, selected_res)

            if filter_props is not None and len(filter_props) > 0:
                selected_res = props_filter(selected_res, filter_props)

            for res in selected_res:
                res_entry = [
                    res.get_name(),
                    res.get_properties(req_props)
                ]
                res_list.append(res_entry)
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
            return fn_rc, res_list
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)

        return fn_rc, None


    def list_volumes(self, res_names, serial, filter_props, req_props):
        """
        Generates a list of resources views suitable for serialized transfer

        Used by the drbdmanage client to display the resources/volumes list
        """
        fn_rc = []

        def resource_filter(res_names):
            for res_name in res_names:
                res = self._resources.get(res_name)
                if res is None:
                    add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT),
                                 [ RES_NAME, res_name ])
                else:
                    yield res

        try:
            res_list = []
            if res_names is not None and len(res_names) > 0:
                selected_res = resource_filter(res_names)
            else:
                selected_res = self._resources.itervalues()
            if serial > 0:
                selected_res = serial_filter(serial, selected_res)

            if filter_props is not None and len(filter_props) > 0:
                selected_res = props_filter(selected_res, filter_props)

            for res in selected_res:
                vol_list = []
                for vol in res.iterate_volumes():
                    vol_entry = [ vol.get_id(), vol.get_properties(None) ]
                    vol_list.append(vol_entry)
                res_entry = [
                    res.get_name(),
                    res.get_properties(req_props), vol_list
                ]
                res_list.append(res_entry)
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
            return fn_rc, res_list
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)

        return fn_rc, None


    def list_assignments(self, node_names, res_names, serial,
        filter_props, req_props):
        """
        Generates a list of assignment views suitable for serialized transfer

        Used by the drbdmanage client to display the assignments list
        """
        fn_rc = []

        def assg_filter(selected_nodes, selected_res):
            for node in selected_nodes.itervalues():
                for res in selected_res.itervalues():
                    assg = node.get_assignment(res.get_name())
                    if assg is not None:
                        yield assg

        try:
            if node_names is not None and len(node_names) > 0:
                selected_nodes = {}
                for node_name in node_names:
                    node = self._nodes.get(node_name)
                    if node is None:
                        add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT),
                                     [ NODE_NAME, node_name ])
                    else:
                        selected_nodes[node.get_name()] = node
            else:
                selected_nodes = self._nodes

            if res_names is not None and len(res_names) > 0:
                selected_res = {}
                for res_name in res_names:
                    res = self._resources.get(res_name)
                    if res is None:
                        add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT),
                                     [ RES_NAME, res_name ])
                    else:
                        selected_res[res.get_name()] = res
            else:
                selected_res = self._resources

            selected_assg = assg_filter(selected_nodes, selected_res)
            if serial > 0:
                selected_assg = serial_filter(serial, selected_assg)

            if filter_props is not None and len(filter_props) > 0:
                selected_assg = props_filter(selected_assg, filter_props)

            assg_list = []
            for assg in selected_assg:
                vol_state_list = []
                for vol_state in assg.iterate_volume_states():
                    vol_state_entry = [
                        vol_state.get_id(),
                        # FIXME: req_props, filter_props, nothing?
                        vol_state.get_properties(None)
                    ]
                    vol_state_list.append(vol_state_entry)
                assg_entry = [
                    assg.get_node().get_name(),
                    assg.get_resource().get_name(),
                    # FIXME: req_props, filter_props, nothing?
                    assg.get_properties(None),
                    vol_state_list
                ]
                assg_list.append(assg_entry)
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
            return fn_rc, assg_list
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)

        return fn_rc, None


    def create_snapshot(self, res_name, snaps_name, node_names, props):
        """
        Create a snapshot of a resource's volumes on a number of nodes
        """
        # Work in progress...
        #
        # create_snapshot(res, name, node[], prop[])
        # |- for each node
        # |  '- check assignment of res on node
        # '- create snapshot in res with name and prop[]
        #    '- for each node
        #       |- create snapshot assignment, link to res and assignment
        #       '- for each volume in res
        #          '- create snapshot volume state and mark as 'deploy'
        fn_rc   = []
        persist = None
        try:
            persist = self.begin_modify_conf()
            if persist is None:
                raise PersistenceException

            # Build a list of the selected nodes and ensure
            # that all of the specified nodes actually exist
            node_list = []
            for node_name in node_names:
                node = self._nodes.get(node_name)
                if node is not None:
                    node_list.append(node)
                else:
                    add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
                    raise AbortException

            # Ensure that the specified resource exists
            resource = self._resources.get(res_name)
            if resource is None:
                add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT))
                raise AbortException

            # Ensure that the specified resource is assigned to all
            # selected nodes
            for node in node_list:
                if node.get_assignment(res_name) is None:
                    add_rc_entry(fn_rc, DM_EINVAL, dm_exc_text(DM_EINVAL))
                    raise AbortException

            # Avoid a name collision with an existing snapshot
            if resource.get_snapshot(snaps_name) is not None:
                add_rc_entry(fn_rc, DM_EEXIST, dm_exc_text(DM_EEXIST))
                raise AbortException

            # Register a new snapshot of the selected resource
            snapshot = DrbdSnapshot(snaps_name, resource,
                self.get_serial, None, None)
            # Merge only auxiliary properties into the
            # Snapshot's properties container
            aux_props = aux_props_selector(props)
            snapshot.get_props().merge_gen(aux_props)
            resource.add_snapshot(snapshot)
            # Register the snapshot assignments
            for node in node_list:
                assignment = node.get_assignment(res_name)
                snaps_assg = DrbdSnapshotAssignment(snapshot, assignment,
                    self.get_serial, None, None)
                # Create snapshot volume state objects
                for vol_state in assignment.iterate_volume_states():
                    cstate = vol_state.get_cstate()
                    tstate = vol_state.get_tstate()
                    # Snapshot volumes that are currently deployed
                    if ((cstate & DrbdVolumeState.FLAG_DEPLOY) != 0 and
                        (tstate & DrbdVolumeState.FLAG_DEPLOY) != 0):
                            snaps_vol_state = DrbdSnapshotVolumeState(
                                vol_state.get_id(),
                                0, DrbdSnapshotVolumeState.FLAG_DEPLOY,
                                None, None,
                                self.get_serial, None, None
                            )
                            snaps_assg.add_snaps_vol_state(snaps_vol_state)
                # Set the snapshot assignment to deploy
                snaps_assg.set_tstate_flags(DrbdSnapshotAssignment.FLAG_DEPLOY)
                snapshot.add_snaps_assg(snaps_assg)
                assignment.add_snaps_assg(snaps_assg)
            self.save_conf_data(persist)
            self.end_modify_conf(persist)
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except AbortException:
            pass
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def list_snapshots(self, res_names, snaps_names, filter_props, req_props):
        """
        List the available snapshots of a resource
        """
        fn_rc = []
        add_rc_entry(fn_rc, DM_ENOTIMPL, dm_exc_text(DM_ENOTIMPL))
        return fn_rc


    def list_snapshot_assignments(self, res_names, snaps_names, node_names,
        filter_props, req_props):
        """
        List the available snapshots of a resource on specific nodes
        """
        fn_rc = []
        add_rc_entry(fn_rc, DM_ENOTIMPL, dm_exc_text(DM_ENOTIMPL))
        return fn_rc


    def restore_snapshot(self, res_name, snaps_name, node_name):
        """
        Restore a snapshot
        """
        fn_rc = []
        add_rc_entry(fn_rc, DM_ENOTIMPL, dm_exc_text(DM_ENOTIMPL))
        return fn_rc


    def delete_snapshot_assignment(self, res_name, snaps_name, node_name):
        """
        Discard a resource's snapshot on a specific node
        """
        fn_rc = []
        add_rc_entry(fn_rc, DM_ENOTIMPL, dm_exc_text(DM_ENOTIMPL))
        return fn_rc


    def delete_snapshot(self, res_name, snaps_name):
        """
        Discard all instances of a resource's snapshot
        """
        fn_rc = []
        add_rc_entry(fn_rc, DM_ENOTIMPL, dm_exc_text(DM_ENOTIMPL))
        return fn_rc


    def query_snapshot(self, res_name, snaps_name):
        """
        Query the state of a resource's snapshot on specific nodes
        """
        fn_rc = []
        add_rc_entry(fn_rc, DM_ENOTIMPL, dm_exc_text(DM_ENOTIMPL))
        return fn_rc


    def save_conf(self):
        """
        Saves the current configuration to the drbdmanage control volume

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = []
        persist  = None
        try:
            persist = persistence_impl(self)
            if persist.open(True):
                self.save_conf_data(persist)
            else:
                raise PersistenceException
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def load_conf(self):
        """
        Loads the current configuration from the drbdmanage control volume

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = []
        persist  = None
        try:
            persist = persistence_impl(self)
            if persist.open(False):
                self.load_conf_data(persist)
            else:
                raise PersistenceException
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def load_conf_data(self, persist):
        """
        Loads the current configuration from the supplied persistence object

        Used by the drbdmanage server to load the configuration after the
        persistence layer had already opened it before

        @return: standard return code defined in drbdmanage.exceptions
        """
        persist.load(self._cluster_conf, self._nodes, self._resources)
        self._conf_hash = persist.get_stored_hash()


    def save_conf_data(self, persist):
        """
        Saves the current configuration to the supplied persistence object

        Used by the drbdmanage server to save the configuration after the
        persistence layer had already opened and locked it before

        @return: standard return code defined in drbdmanage.exceptions
        """
        hash_obj = None
        persist.save(self._cluster_conf, self._nodes, self._resources)
        hash_obj = persist.get_hash_obj()
        if hash_obj is not None:
            self._conf_hash = hash_obj.get_hex_hash()


    def open_conf(self):
        """
        Opens the configuration on persistent storage for reading
        This function is only there because drbdcore cannot import anything
        from persistence, so the code for creating a PersistenceImpl object
        has to be somwhere else.
        Returns a PersistenceImpl object on success, or None if the operation
        fails due to errors in the persistence layer

        @return: persistence layer object
        """
        ret_persist = None
        persist     = None
        try:
            persist = persistence_impl(self)
            if persist.open(False):
                ret_persist = persist
        except Exception as exc:
            # DEBUG
            exc_type, exc_obj, exc_tb = sys.exc_info()
            logging.error(
                "cannot open control volume, unhandled exception: %s"
                % str(exc)
            )
            logging.debug("Stack trace:\n%s" % str(exc_tb))
            persist.close()
        return ret_persist


    def begin_modify_conf(self):
        """
        Opens the configuration on persistent storage for writing,
        implicitly locking out all other nodes, and reloads the configuration
        if it has changed.
        Returns a PersistenceImpl object on success, or None if the operation
        fails due to errors in the persistence layer

        @return: persistence layer object
        """
        ret_persist = None
        persist     = None
        try:
            persist = persistence_impl(self)
            if persist.open(True):
                if not self.hashes_match(persist.get_stored_hash()):
                    self.load_conf_data(persist)
                ret_persist = persist
        except Exception as exc:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            logging.error(
                "cannot open the control volume for modification, "
                "unhandled exception: %s"
                % str(exc)
            )
            logging.debug("Stack trace:\n%s" % str(exc_tb))
            persist.close()
        return ret_persist


    def end_modify_conf(self, persist):
        """
        Closes the configuration on persistent storage.

        @param   persist: persistence layer object to close
        """
        try:
            if persist is not None:
                persist.close()
            self.close_serial()
        except Exception:
            pass


    # TODO: more precise error handling
    def export_conf(self, res_name):
        """
        For a named resource, exports a configuration file for drbdadm

        Exports a configuration file for drbdadm generated from the current
        configuration of a resource managed by the drbdmanage server on the
        current host.
        If the resource name is "*", configuration files for all resources
        currently deployed on the current host are generated.

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = []
        node = self.get_instance_node()
        if node is not None:
            if res_name is None:
                res_name = ""
            if len(res_name) > 0 and res_name != "*":
                assg = node.get_assignment(res_name)
                if assg is not None:
                    if self.export_assignment_conf(assg) != 0:
                        add_rc_entry(fn_rc, DM_DEBUG, dm_exc_text(DM_DEBUG))
                else:
                    add_rc_entry(fn_rc, DM_ENOENT, dm_exc_text(DM_ENOENT),
                                 RES_NAME, res_name)
            else:
                for assg in node.iterate_assignments():
                    if self.export_assignment_conf(assg) != 0:
                        add_rc_entry(fn_rc, DM_DEBUG, dm_exc_text(DM_DEBUG))
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    # TODO: move over existing file instead of directly overwriting an
    #       existing file
    def export_assignment_conf(self, assignment):
        """
        From an assignment object, exports a configuration file for drbdadm

        Exports a configuration file for drbdadm generated from the current
        configuration of an assignment object managed by the drbdmanage server

        The drbdmanage server uses this function to generate temporary
        configuration files for drbdadm callbacks by the DRBD kernel module
        as well.

        @return: 0 on success, 1 on error
        """
        fn_rc = 0
        resource = assignment.get_resource()
        file_path = self._conf[self.KEY_DRBD_CONFPATH]
        if not file_path.endswith("/"):
            file_path += "/"
        file_path += "drbdmanage_" + resource.get_name() + ".res"
        assg_conf = None
        try:
            assg_conf = open(file_path, "w")
            writer    = DrbdAdmConf()
            writer.write(assg_conf, assignment, False)
        except IOError as ioerr:
            logging.error(
                "cannot write to configuration file '%s', error "
                "returned by the OS is: %s"
                % (file_path, ioerr.strerror)
            )
            fn_rc = 1
        finally:
            if assg_conf is not None:
                assg_conf.close()
        return fn_rc


    def remove_assignment_conf(self, resource_name):
        """
        Removes (unlinks) a drbdadm configuration file

        The drbdmanage server uses this function to remove configuration files
        of resources that become undeployed on the current host.

        @return: 0 on success, 1 on error
        """
        fn_rc = 0
        file_path = self._conf[self.KEY_DRBD_CONFPATH]
        if not file_path.endswith("/"):
            file_path += "/"
        file_path += "drbdmanage_" + resource_name + ".res"
        try:
            os.unlink(file_path)
        except OSError as oserr:
            logging.error(
                "cannot remove configuration file '%s', "
                "error returned by the OS is: %s"
                % (file_path, oserr.strerror)
            )
            fn_rc = 1
        return fn_rc


    def get_conf_hash(self):
        """
        Retrieves the hash code of the currently loaded configuration

        @return: hash code of the currently loaded configuration
        @rtype:  str
        """
        return self._conf_hash


    def hashes_match(self, cmp_hash):
        """
        Checks whether the currently known hash matches the supplied hash

        Configuration changes on the drbdmanage control volume are detected
        by checking whether the hash has changed. This is done by comparing
        the hash of the currently known configuration to the hash stored on
        the control volume whenever the data on the control volume may have
        changed.

        @return: True if the hashes match, False otherwise
        @rtype:  bool
        """
        if self._conf_hash is not None and cmp_hash is not None:
            if self._conf_hash == cmp_hash:
                return True
        return False


    def reconfigure(self):
        """
        Reconfigures the server

        @return: standard return code defined in drbdmanage.exceptions
        """
        fn_rc = []
        try:
            self.load_server_conf()
            fn_rc = self.load_conf()
            self._drbd_mgr.reconfigure()
            self._bd_mgr = BlockDeviceManager(self._conf[self.KEY_STOR_NAME])
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def init_node(self, name, props, bdev, port):
        """
        Server part of initializing a new drbdmanage cluster
        """
        fn_rc   = []
        persist = None
        try:
            persist = self.begin_modify_conf()

            # clear the configuration
            self._nodes     = {}
            self._resources = {}

            if persist is not None:
                sub_rc = self._create_node(True, name, props, bdev, port)
                if sub_rc == DM_SUCCESS or sub_rc == DM_ECTRLVOL:
                    # attempt to determine the amount of total and free
                    # storage space on the local node; if that fails, total
                    # and free space will be determined later, either when
                    # volumes are created or when the drbdmanage server
                    # is restarted
                    # therefore, the return code is intentionally ignored
                    self.update_pool_data()
                    # save the changes to the control volume
                    self.save_conf_data(persist)
                else:
                    add_rc_entry(fn_rc, sub_rc, dm_exc_text(sub_rc))
            else:
                raise PersistenceException
        except PersistenceException:
            add_rc_entry(fn_rc, DM_EPERSIST, dm_exc_text(DM_EPERSIST))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        finally:
            self.end_modify_conf(persist)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def join_node(self, bdev, port, secret):
        """
        Server part of integrating a node into an existing drbdmanage cluster
        """
        fn_rc = []
        try:
            # TODO: there should probably be library functions for evaluating
            #       return code lists
            fn_rc = self.load_conf()
            load_ok = False
            for rc_entry in fn_rc:
                if rc_entry[0] == DM_SUCCESS:
                    load_ok = True
                else:
                    load_ok = False
                    break
            if load_ok:
                if (self._configure_drbdctrl(True, secret, bdev, port) == 0):
                    self._drbd_mgr.adjust_drbdctrl()
                else:
                    # delete the success entry from load_conf() from the list
                    # and append the control volume error code
                    del fn_rc[:]
                    add_rc_entry(fn_rc, DM_ECTRLVOL, dm_exc_text(DM_ECTRLVOL))
        except Exception as exc:
            DrbdManageServer.catch_and_append_internal_error(fn_rc, exc)
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc


    def TQ_joinc(self, node_name=None):
        if not node_name:
            return [("Error: joinc query without a node name argument")]

        fields    = None
        secret    = None
        bdev      = None
        port      = None
        l_addr    = None
        l_node_id = None
        r_addr    = None
        r_node_id = None
        r_name    = None

        drbdctrl_res = None

        conffile = DrbdAdmConf()
        try:
            drbdctrl_res = open(
                build_path(DRBDCTRL_RES_PATH, DRBDCTRL_RES_FILE),
                "r"
            )
            fields = conffile.read_drbdctrl_params(drbdctrl_res)
        except (IOError, OSError):
            pass
        finally:
            if drbdctrl_res is not None:
                try:
                    drbdctrl_res.close()
                except (IOError, OSError):
                    pass

        try:
            address = fields[DrbdAdmConf.KEY_ADDRESS]
            idx = address.rfind(":")
            if idx != -1:
                port = address[idx + 1:]
            else:
                raise ValueError
            secret = fields[DrbdAdmConf.KEY_SECRET]
            update = True
        except (KeyError, ValueError):
            pass

        inst_node = self.get_instance_node()
        rem_node  = self._nodes.get(node_name)

        if inst_node is not None and rem_node is not None:
            r_addr    = inst_node.get_addr()
            r_node_id = str(inst_node.get_node_id())
            r_name    = inst_node.get_name()
            l_addr    = rem_node.get_addr()
            l_node_id = str(rem_node.get_node_id())

        if (all([secret, port, r_addr, r_node_id, r_name, l_addr, l_node_id])):
            return ["drbdmanage", "join", "-p",
                    port, l_addr, l_node_id, r_addr, r_name,
                    r_node_id, secret]
        else:
            return [("Error: Generation of the join command failed")]


    def TQ_get_path(self, res_name, vol_id_arg="0"):
        """ Get path of device node.
            res_name is needed, vol_id is optional. """
        # TODO: can this be per-node specific?
        resource = self._resources.get(res_name)
        # TODO: throw exceptions?
        if resource is None:
            return ["Resource not found"]
        vol_id = int(vol_id_arg)
        volume = resource._volumes.get(vol_id)
        if volume is None:
            return ["Invalid volume id"]
        return [volume.get_path()]


    def TQ_export_conf(self, node_name, res_name):
        """
        Export the configuration file for a DRBD resource on a specified node
        """
        response = []
        node     = self._nodes.get(node_name)
        resource = self._resources.get(res_name)
        if node is not None and resource is not None:
            assignment = node.get_assignment(res_name)
            if assignment is not None:
                conf_buffer = StringIO.StringIO()
                writer = DrbdAdmConf()
                writer.write(conf_buffer, assignment, False)
                response = [conf_buffer.getvalue()]
                conf_buffer.close()
            else:
                response = [
                    "Error: Resource %s is not assigned to node %s"
                    % (res_name, node_name)
                ]
        else:
            if node is None:
                response = ["Error: Node %s not found" % (node_name)]
            else:
                response = ["Error: Resource %s not found" % (res_name)]
        return response


    def text_query(self, command):
        """
        Query text strings from the server

        @param   command: query command and argument list
        @type    command: list of str
        @return: list of answer texts to the query
        @rtype:  list of str
        """
        fn_rc = []
        result_text = []
        try:
            if len(command) < 1:
                add_rc_entry(fn_rc, DM_EINVAL, dm_exc_text(DM_EINVAL))
                return (
                    fn_rc,
                    [
                        "Error: empty argument list sent to the "
                        "drbdmanage server"
                    ]
                )

            func_name = "TQ_" + command.pop(0)
            text_query_func = getattr(self, func_name)
            if text_query_func is None:
                result_text = ["Error: unknown command"]
            else:
                # optional arguments are those that have default values
                # specified in the function declaration
                (mandatory_args, _, _, optional_args) = (
                    inspect.getargspec(text_query_func)
                )
                # remove the "self" argument
                mandatory_args.pop(0)

                mandatory_args_len = len(mandatory_args)
                optional_args_len  = (0 if optional_args is None
                                      else len(optional_args))
                command_len        = len(command)
                # TODO: varargs
                if command_len > mandatory_args_len:
                    result_text = ["Error: too many arguments."]
                elif command_len + optional_args_len < mandatory_args_len:
                    result_text = ["Error: too few arguments."]
                else:
                    result_text = text_query_func(*command)
        except Exception as exc:
            # FIXME: useful error messages required here
            logging.error("text_query() command failed: %s" % str(exc))
            add_rc_entry(fn_rc, DM_DEBUG, dm_exc_text(DM_DEBUG))
            return (
                fn_rc,
                [
                    "Error: Text query command failed on the "
                    "drbdmanage server"
                ]
            )
        if len(fn_rc) == 0:
            add_rc_entry(fn_rc, DM_SUCCESS, dm_exc_text(DM_SUCCESS))
        return fn_rc, result_text


    def reconfigure_drbdctrl(self):
        """
        Updates the current node's control volume configuration
        """
        self._configure_drbdctrl(False, None, None, None)
        self._drbd_mgr.adjust_drbdctrl()


    def _configure_drbdctrl(self, initial, secret, bdev, port):
        """
        Creates or updates the drbdctrl resource configuration file
        """
        # if values are missing, try to get those values from an existing
        # configuration file; if no configuration file can be read,
        # use default values
        fn_rc        = 1
        drbdctrl_res = None
        conffile     = DrbdAdmConf()
        update       = False

        if (secret is not None and bdev is not None and port is not None):
            update = True
        else:
            # Load values from an existing configuation unless all values are
            # specified or an initial configuration is requested
            if not initial:
                fields = None

                # Try to open an existing configuration file
                try:
                    drbdctrl_res = open(
                        build_path(DRBDCTRL_RES_PATH, DRBDCTRL_RES_FILE),
                        "r"
                    )
                except (IOError, OSError):
                    # if the drbdctrl.res file cannot be opened, assume
                    # that it does not exist and create a new one
                    update = True

                # If an existing configuration file can be read, try to extract
                # values from the configuration file
                if not update:
                    try:
                        fields = conffile.read_drbdctrl_params(drbdctrl_res)
                    except (IOError, OSError):
                        pass
                    finally:
                        if drbdctrl_res is not None:
                            try:
                                drbdctrl_res.close()
                            except (IOError, OSError):
                                pass
                    if fields is not None:
                        try:
                            if port is None:
                                address = fields[DrbdAdmConf.KEY_ADDRESS]
                                idx = address.rfind(":")
                                if idx != -1:
                                    port = address[idx + 1:]
                                else:
                                    raise ValueError
                            if bdev is None:
                                bdev = fields[DrbdAdmConf.KEY_BDEV]
                            if secret is None:
                                secret = fields[DrbdAdmConf.KEY_SECRET]
                            update = True
                        except (KeyError, ValueError):
                            pass

        # if an existing configuration has been read successfully,
        # or an initial configuration file should be created,
        # write the drbdctrl.res file
        if initial or update:
            try:
                # use defaults for anything that is still unset
                if port is None:
                    port = str(DRBDCTRL_DEFAULT_PORT)
                if bdev is None:
                    bdev = ("/dev/" + self.get_conf_value(KEY_DRBDCTRL_VG) +
                            "/" + DRBDCTRL_RES_NAME)
                if secret is None:
                    secret = generate_secret()

                drbdctrl_res = open(
                    build_path(DRBDCTRL_RES_PATH, DRBDCTRL_RES_FILE), "w")
                conffile.write_drbdctrl(drbdctrl_res, self._nodes,
                                        bdev, port, secret)
                drbdctrl_res.close()
                fn_rc = 0
            except (IOError, OSError):
                pass
            finally:
                if drbdctrl_res is not None:
                    try:
                        drbdctrl_res.close()
                    except (IOError, OSError):
                        pass
        return fn_rc


    def debug_console(self, cmdline):
        """
        Set debugging options
        """
        fn_rc = 127
        try:
            args = CmdLineReader(cmdline)
            command = args.next_arg()
            if command is not None:
                if command == "set":
                    try:
                        subcommand = args.next_arg()
                        if subcommand == "n":
                            fn_rc == self._debug_set_node(args)
                        elif subcommand == "r":
                            fn_rc = self._debug_set_resource(args)
                        elif subcommand == "v":
                            fn_rc = self._debug_set_volume(args)
                        elif subcommand == "a":
                            fn_rc = self._debug_set_assignment(args)
                        elif subcommand == "s":
                            fn_rc = self._debug_set_snapshot(args)
                        elif subcommand == "s/a":
                            fn_rc = self._debug_set_snapshot_assignment(args)
                        else:
                            key, val = self._debug_keyval_split(subcommand)
                            if key == "dbg_events":
                                self.dbg_events = self._debug_parse_flag(val)
                                fn_rc = 0
                            elif key == "loglevel":
                                loglevel = self._debug_parse_loglevel(val)
                                self._root_logger.setLevel(loglevel)
                                fn_rc = 0
                    except AttributeError:
                        fn_rc = 1
                elif command == "run":
                    try:
                        item = args.next_arg()
                        if item == "cleanup":
                            self.cleanup()
                            fn_rc = 0
                        elif item == "DrbdManager":
                            # override the hash check, but do not poke
                            # the cluster
                            self._drbd_mgr.run(True, False)
                            fn_rc = 0
                    except AttributeError:
                        pass
                elif command == "restart":
                    try:
                        item = args.next_arg()
                        if item == "events":
                            self.restart_events(None, None)
                            fn_rc = 0
                    except AttributeError:
                        pass
                elif command == "test":
                    try:
                        item = args.next_arg()
                        if item == "stdout":
                            sys.stdout.write("(test stdout)\n")
                            fn_rc = 0
                        elif item == "stderr":
                            sys.stderr.write("(test stderr)\n")
                            fn_rc = 0
                    except AttributeError:
                        pass
                elif command == "list":
                    try:
                        item = args.next_arg()
                        if item == "n":
                            fn_rc = self._debug_list_nodes(args)
                        elif item == "r":
                            fn_rc = self._debug_list_resources(args)
                        elif item == "v":
                            fn_rc = self._debug_list_volumes(args)
                        elif item == "a":
                            fn_rc = self._debug_list_assignments(args)
                        elif item == "s":
                            fn_rc = self._debug_list_snapshots(args)
                        elif item == "s/a":
                            fn_rc = self._debug_list_snapshot_assignments(args)
                        elif item == "conf/server":
                            fn_rc = self._debug_list_server_conf(args)
                        elif item == "conf/cluster":
                            fn_rc = self._debug_list_cluster_conf(args)
                        elif item == "props":
                            fn_rc = self._debug_list_props(args)
                    except AttributeError:
                        pass
                elif command == "gen":
                    try:
                        item = args.next_arg()
                        if item == "drbdctrl":
                            fn_rc = self._debug_gen_drbdctrl(args)
                    except AttributeError:
                        pass
                elif command == "mod":
                    try:
                        item = args.next_arg()
                        if item == "drbdctrl":
                            fn_rc = self._debug_mod_drbdctrl(args)
                    except AttributeError:
                        pass
                elif command == "invalidate":
                    self._conf_hash = None
                    fn_rc = 0
                elif command == "show":
                    try:
                        subcommand = args.next_arg()
                        if subcommand == "hash":
                            if self._conf_hash is None:
                                sys.stderr.write("unset/invalid\n")
                            else:
                                sys.stderr.write("%s\n" % (self._conf_hash))
                            fn_rc = 0
                    except AttributeError:
                        pass
                elif command == "exit":
                    try:
                        exit_code_str = args.next_arg()
                        exit_code     = int(exit_code_str)
                        exit_msg = ("server shutdown (debug command): exit %d"
                                    % (exit_code))
                        sys.stderr.write(exit_msg + "\n")
                        logging.debug(exit_msg)
                        exit(exit_code)
                    except (ValueError, AttributeError):
                        pass
        except SyntaxException:
            fn_rc = 1
        except Exception as exc:
            DrbdManageServer.catch_internal_error(exc)
            fn_rc = DM_DEBUG
        return fn_rc


    def _debug_gen_drbdctrl(self, args):
        fn_rc = 1
        secret = args.next_arg()
        port   = args.next_arg()
        bdev   = args.next_arg()
        fn_rc  = self._configure_drbdctrl(True, secret, bdev, port)
        return fn_rc


    def _debug_mod_drbdctrl(self, args):
        fn_rc = 1
        secret = args.next_arg()
        port   = args.next_arg()
        bdev   = args.next_arg()
        fn_rc  = self._configure_drbdctrl(False, secret, bdev, port)
        return fn_rc


    def _debug_list_nodes(self, args):
        fn_rc = 1
        title = "list: nodes"
        nodename = args.next_arg()
        if nodename is not None:
            node = self._nodes.get(nodename)
            if node is not None:
                self._debug_section_begin(title)
                self._debug_dump_node(node)
                self._debug_section_end(title)
                fn_rc = 0
            else:
                sys.stderr.write("Node '%s' not found\n" % (nodename))
        else:
            self._debug_section_begin(title)
            for node in self._nodes.itervalues():
                self._debug_dump_node(node)
            self._debug_section_end(title)
            fn_rc = 0
        return fn_rc


    def _debug_list_resources(self, args):
        fn_rc = 1
        title = "list: resources"
        resname = args.next_arg()
        if resname is not None:
            resource = self._resources.get(resname)
            if resource is not None:
                self._debug_section_begin(title)
                self._debug_dump_resource(resource)
                self._debug_section_end(title)
                fn_rc = 0
            else:
                sys.stderr.write("Resource '%s' not found\n" % (resname))
        else:
            self._debug_section_begin(title)
            for resource in self._resources.itervalues():
                self._debug_dump_resource(resource)
            self._debug_section_end(title)
            fn_rc = 0
        return fn_rc


    def _debug_list_volumes(self, args):
        fn_rc = 1
        title = "list: resources"
        resname = args.next_arg()
        if resname is not None:
            resource = self._resources.get(resname)
            if resource is not None:
                self._debug_section_begin(title)
                self._debug_dump_volumes(resource)
                self._debug_section_end(title)
                fn_rc = 0
            else:
                sys.stderr.write("Resource '%s' not found\n" % (resname))
        else:
            self._debug_section_begin(title)
            for resource in self._resources.itervalues():
                self._debug_dump_volumes(resource)
            self._debug_section_end(title)
            fn_rc = 0
        return fn_rc


    def _debug_list_assignments(self, args):
        fn_rc = 1
        title = "list: assignments"
        objname = args.next_arg()
        if objname is not None:
            if objname.find("@") == 0:
                nodename = objname[1:]
                node = self._nodes.get(nodename)
                if node is not None:
                    self._debug_section_begin(title)
                    for assg in node.iterate_assignments():
                        self._debug_dump_assignment(assg)
                    self._debug_section_end(title)
                    fn_rc = 0
                else:
                    sys.stderr.write("Node '%s' not found\n" % (nodename))
            else:
                resource = self._resources.get(objname)
                if resource is not None:
                    self._debug_section_begin(title)
                    for assg in resource.iterate_assignments():
                        self._debug_dump_assignment(assg)
                    self._debug_section_end(title)
                    fn_rc = 0
                else:
                    sys.stderr.write("Resource '%s' not found\n" % (objname))
        else:
            self._debug_section_begin(title)
            for node in self._nodes.itervalues():
                for assg in node.iterate_assignments():
                    self._debug_dump_assignment(assg)
            self._debug_section_end(title)
            fn_rc = 0
        return fn_rc


    def _debug_list_snapshots(self, args):
        fn_rc = 1
        title = "list: snapshots"
        resname = args.next_arg()
        if resname is not None:
            resource = self._resources.get(resname)
            if resource is not None:
                self._debug_section_begin(title)
                for snapshot in resource.iterate_snapshots:
                    self._debug_dump_snapshot(snapshot)
                self._debug_section_end(title)
                fn_rc = 0
            else:
                sys.stderr.write("Resource '%s' not found\n" % (resname))
        else:
            self._debug_section_begin(title)
            for resource in self._resources.itervalues():
                for snapshot in resource.iterate_snapshots():
                    self._debug_dump_snapshot(snapshot)
            self._debug_section_end(title)
            fn_rc = 0
        return fn_rc


    def _debug_list_snapshot_assignments(self, args):
        fn_rc = 1
        title = "list: snapshot assignments"
        resname   = args.next_arg()
        snapsname = args.next_arg()
        if resname is not None and snapsname is not None:
            resource = self._resources.get(resname)
            if resource is not None:
                snapshot = resource.get_snapshot(snapsname)
                if snapshot is not None:
                    self._debug_section_begin(title)
                    for snaps_assg in snapshot.iterate_snaps_assg():
                        self._debug_dump_snapshot_assignment(snaps_assg)
                    self._debug_section_end(title)
                    fn_rc = 0
                else:
                    sys.stderr.write("Snapshot '%s' not found\n" % (snapsname))
            else:
                sys.stderr.write("Resource '%s' not found\n" % (resname))
        else:
            self._debug_section_begin(title)
            for resource in self._resources.itervalues():
                for snapshot in resource.iterate_snapshots():
                    for snaps_assg in snapshot.iterate_snaps_assg():
                        self._debug_dump_snapshot_assignment(snaps_assg)
            self._debug_section_end(title)
            fn_rc = 0
        return fn_rc


    def _debug_list_props(self, args):
        fn_rc        = 1
        title        = "list: object properties"
        props_format = "%-30s = %s\n"
        obj_class    = args.next_arg()
        obj_name     = args.next_arg()
        prop_key     = args.next_arg()
        props        = None
        if obj_class == "n":
            node = self._nodes.get(obj_name)
            if node is not None:
                props = node.get_props()
            else:
                sys.stderr.write("Node '%s' not found\n" % (obj_name))
        elif obj_class == "r":
            resource = self._resources.get(obj_name)
            if resource is not None:
                props = resource.get_props()
            else:
                sys.stderr.write("Resource '%s' not found\n" % (obj_name))
        elif obj_class == "v":
            split_idx = obj_name.find("/")
            if split_idx != -1:
                resname  = obj_name[:split_idx]
                resource = self._resources.get(resname)
                if resource is not None:
                    vol_nr = obj_name[split_idx + 1:]
                    try:
                        vol_id   = int(vol_nr)
                        volume   = resource.get_volume(vol_id)
                        if volume is not None:
                            props = volume.get_props()
                        else:
                            sys.stderr.write(
                                "Resource '%s' has no volume %d\n"
                                % (resname, vol_id)
                            )
                    except ValueError:
                        sys.stderr.write("Invalid volume id '%s'\n" % (vol_id))
                else:
                    sys.stderr.write("Resource '%s' not found\n" % (resname))
            else:
                resource = self._resources.get(obj_name)
                if resource is not None:
                    props = resource.get_props()
                else:
                    sys.stderr.write("Resource '%s' not found\n" % (obj_name))
        elif obj_class == "a":
            split_idx = obj_name.find("/")
            if split_idx != -1:
                nodename = obj_name[:split_idx]
                resname  = obj_name[split_idx + 1:]
                vol_nr   = None
                split_idx = resname.find("/")
                if split_idx != -1:
                    vol_nr  = resname[split_idx + 1:]
                    resname = resname[:split_idx]
                node     = self._nodes.get(nodename)
                resource = self._resources.get(resname)
                if node is not None and resource is not None:
                    assg = node.get_assignment(resource.get_name())
                    if assg is not None:
                        if vol_nr is not None:
                            try:
                                vol_id    = int(vol_nr)
                                vol_state = assg.get_volume_state(vol_id)
                                if vol_state is not None:
                                    props = vol_state.get_props()
                                else:
                                    sys.stderr.write(
                                        "Assignment '%s/%s' has no state for "
                                        "volume %d\n"
                                        % (node.get_name(),
                                           resource.get_name(),
                                           vol_id)
                                    )
                            except ValueError:
                                sys.stderr.write(
                                    "Invalid volume id '%s'\n"
                                    % (vol_nr)
                                )
                        else:
                            props = assg.get_props()
                    else:
                        sys.stderr.write(
                            "Assignment '%s/%s' not found\n"
                            % (node.get_name(), resource.get_name())
                        )
                else:
                    if resource is None:
                        sys.stderr.write(
                            "Resource '%s' not found\n"
                            % (resname)
                        )
                    if node is None:
                        sys.stderr.write(
                            "Node '%s' not found\n"
                            % (nodename)
                        )
        else:
            sys.stderr.write("Unknown object class '%s'\n" % (obj_class))
        if props is not None:
            if prop_key is None:
                self._debug_section_begin(title)
                for (prop_key, props_val) in props.iteritems():
                    sys.stderr.write(props_format % (prop_key, props_val))
                self._debug_section_end(title)
                fn_rc = 0
            else:
                try:
                    props_val = props.get_prop(prop_key)
                    self._debug_section_begin(title)
                    sys.stderr.write(props_format % (prop_key, props_val))
                    self._debug_section_end(title)
                    fn_rc = 0
                except KeyError:
                    sys.stderr.write("Property '%s' not found\n" % prop_key)
        return fn_rc


    def _debug_list_server_conf(self, args):
        title = "list: server configuration"
        self._debug_section_begin(title)
        self._debug_list_conf(args, self._conf)
        self._debug_section_end(title)
        return 0


    def _debug_list_cluster_conf(self, args):
        title = "list: cluster configuration"
        self._debug_section_begin(title)
        self._debug_list_conf(args, self._cluster_conf)
        self._debug_section_end(title)
        return 0


    def _debug_list_conf(self, args, conf):
        keyval_format    = "%-30s = %s\n"
        key_unset_format = "Key '%s' not found\n"
        val_unset_format = "%-30s is unset\n"
        key = args.next_arg()
        if key is not None:
            try:
                val = conf[key]
                if val is not None:
                    sys.stderr.write(keyval_format % (key, val))
                else:
                    sys.stderr.write(val_unset_format % (key))
            except KeyError:
                sys.stderr.write(key_unset_format % (key))
        else:
            for (key, val) in conf.iteritems():
                if val is not None:
                    sys.stderr.write(keyval_format % (key, val))
                else:
                    sys.stderr.write(val_unset_format % (key))


    def _debug_dump_node(self, node):
        sys.stderr.write(
            "  ID:%-18s AF:%-2u ADDR:%-16s S:0x%.16x\n"
            % (node.get_name(), node.get_addrfam(),
               node.get_addr(), node.get_state())
        )


    def _debug_dump_resource(self, resource):
        sys.stderr.write(
            "  ID:%-18s P:%.5u S:0x%.16x\n"
            % (resource.get_name(), int(resource.get_port()),
               resource.get_state())
        )


    def _debug_dump_volumes(self, resource):
        sys.stderr.write(
            "  R/ID:%-18s\n"
            % (resource.get_name())
        )
        for volume in resource.iterate_volumes():
            vol_size_kiB = volume.get_size_kiB()
            sys.stderr.write(
                "  * V/ID:%.5u M:%.7u SIZE:%.13u S:0x%.16x\n"
                % (volume.get_id(), volume.get_minor().get_value(),
                   vol_size_kiB, volume.get_state())
            )


    def _debug_dump_assignment(self, assg):
        node     = assg.get_node()
        resource = assg.get_resource()
        sys.stderr.write(
            "  N/ID:%-18s R/ID:%-18s\n"
            % (node.get_name(), resource.get_name())
        )
        sys.stderr.write(
            "  '- S/C:0x%.16x S/T:0x%.16x\n"
            % (assg.get_cstate(),
               assg.get_tstate())
        )
        for vol_state in assg.iterate_volume_states():
            vol_bdev_path = vol_state.get_bd_path()
            if vol_bdev_path is None:
                vol_bdev_path = "(unset)"
            sys.stderr.write(
                "  * V/ID:%.5u S/C:0x%.16x S/T:0x%.16x\n"
                % (vol_state.get_id(),
                   vol_state.get_cstate(),
                   vol_state.get_tstate())
            )
            sys.stderr.write(
                "  '- BD:%s\n" % (vol_bdev_path)
            )


    def _debug_dump_snapshot(self, snapshot):
        resource = snapshot.get_resource()
        sys.stderr.write(
            "  R/ID:%-18s S/ID:%-18s\n"
            % (resource.get_name(), snapshot.get_name())
        )


    def _debug_dump_snapshot_assignment(self, snaps_assg):
        assg     = snaps_assg.get_assignment()
        snapshot = snaps_assg.get_snapshot()
        node     = assg.get_node()
        resource = assg.get_resource()
        sys.stderr.write(
            "  R/ID:%-18s S/ID:%-18s N/ID:%-18s\n"
            % (resource.get_name(), snapshot.get_name(), node.get_name())
        )
        sys.stderr.write(
            "  '- S/C:0x%.16x S/T:0x%.16x\n"
            % (snaps_assg.get_cstate(), snaps_assg.get_tstate())
        )
        for snaps_vol_state in snaps_assg.iterate_snaps_vol_states():
            sys.stderr.write(
                "  * V/ID:%.5u S/C:0x%.16x S/T:0x%.16x\n"
                % (snaps_vol_state.get_id(), snaps_vol_state.get_cstate(),
                   snaps_vol_state.get_tstate())
            )


    def _debug_set_node(self, args):
        fn_rc = 1
        nodename = args.next_arg()
        if nodename is not None:
            node = self._nodes.get(nodename)
            if node is not None:
                keyval = args.next_arg()
                key, val = self._debug_keyval_split(keyval)
                if key == "state":
                    try:
                        state_update = long(val)
                        node.set_state(state_update)
                        fn_rc = 0
                    except ValueError:
                        pass
            else:
                sys.stderr.write("Node '%s' not found\n" % (nodename))
        return fn_rc


    def _debug_set_resource(self, args):
        fn_rc = 1
        resname = args.next_arg()
        if resname is not None:
            resource = self._resources.get(resname)
            if resource is not None:
                keyval = args.next_arg()
                key, val = self._debug_keyval_split(keyval)
                if key == "state":
                    try:
                        state_update = long(val)
                        resource.set_state(state_update)
                        fn_rc = 0
                    except ValueError:
                        pass
            else:
                sys.stderr.write("Resource '%s' not found\n" % (resname))
        return fn_rc


    def _debug_set_volume(self, args):
        fn_rc = 1
        resname    = args.next_arg()
        if resname is not None:
            vol_id_str = None
            split_idx  = resname.find("/")
            if split_idx != -1:
                vol_id_str = resname[split_idx + 1:]
                resname    = resname[:split_idx]
            resource = self._resources.get(resname)
            if resource is not None and vol_id_str is not None:
                try:
                    vol_id = int(vol_id_str)
                    volume = resource.get_volume(vol_id)
                    if volume is not None:
                        keyval = args.next_arg()
                        key, val = self._debug_keyval_split(keyval)
                        if key == "state":
                            state_update = long(val)
                            volume.set_state(state_update)
                            fn_rc = 0
                    else:
                        sys.stderr.write(
                            "Invalid volume index %u for resource '%s'\n"
                            % (vol_id, resource.get_name())
                        )
                except ValueError:
                    pass
            else:
                sys.stderr.write("Resource '%s' not found\n" % (resname))
        return fn_rc


    def _debug_set_assignment(self, args):
        fn_rc = 1
        nodename   = args.next_arg()
        resname    = None
        split_idx  = nodename.find("/")
        if split_idx != -1:
            resname  = nodename[split_idx + 1:]
            nodename = nodename[:split_idx]
        if nodename is not None and resname is not None:
            node     = self._nodes.get(nodename)
            resource = self._resources.get(resname)
            if node is not None and resource is not None:
                assg = node.get_assignment(resource.get_name())
                if assg is not None:
                    try:
                        keyval = args.next_arg()
                        key, val = self._debug_keyval_split(keyval)
                        if key == "cstate":
                            state_update = long(val)
                            assg.set_cstate(state_update)
                            fn_rc = 0
                        elif key == "tstate":
                            state_update = long(val)
                            assg.set_tstate(state_update)
                            fn_rc = 0
                    except ValueError:
                        pass
                else:
                    sys.stderr.write(
                        "Resource '%s' is not assigned to node '%s'\n"
                        % (resource.get_name(), node.get_name())
                    )
            else:
                if node is None:
                    sys.stderr.write("Node '%s' not found\n" % (nodename))
                if resource is None:
                    sys.stderr.write("Resource '%s' not found\n" % (resname))
        return fn_rc


    def _debug_set_snapshot(self, args):
        return 1


    def _debug_set_snapshot_assignment(self, args):
        return 1


    def _debug_parse_flag(self, val):
        """
        Convert a string argument to boolean values
        """
        if val == "1":
            flag = True
        elif val == "0":
            flag = False
        else:
            raise SyntaxException
        return flag


    def _debug_parse_loglevel(self, val):
        """
        Convert a string argument to a standard log level
        """
        loglevel = None
        try:
            loglevel = self.DM_LOGLEVELS[val.upper()]
        except KeyError:
            raise SyntaxException
        return loglevel


    def _debug_keyval_split(self, keyval):
        split_idx = keyval.find("=")
        key = keyval[:split_idx].lower()
        val = keyval[split_idx + 1:]
        return (key, val)


    def _debug_section_begin(self, title):
        self._debug_section_generic("BEGIN:", title)


    def _debug_section_end(self, title):
        self._debug_section_generic("END:", title)


    def _debug_section_generic(self, prefix, title):
        # the prefix should not be longer than 6 characters
        section_ruler = "== DEBUG == %-6s %s ==" % (prefix, title)
        title_len = len(title)
        # extend the "=" line up to a total length of 75
        # characters (added up with the text prefix, that's
        # the magic '53' remaining characters here)
        repeat = 53 - title_len if title_len <= 53 else 0
        section_ruler += ("=" * repeat) + "\n"
        sys.stderr.write(section_ruler)


    def shutdown(self):
        """
        Stops this drbdmanage server instance
        """
        logging.info("server shutdown (requested by function call)")
        logging.info("shutting down the control volume")
        self._drbd_mgr.down_drbdctrl()
        logging.info("server shutdown complete, exiting")
        # FIXME: Maybe the drbdsetup child process should be terminated first?
        exit(0)


    def get_free_minor_nr(self):
        """
        Retrieves a free (unused) minor number

        Minor numbers are allocated in the range from the configuration value
        KEY_MIN_MINOR_NR to the constant MinorNr.MINOR_NR_MAX. A minor number
        that is unique across the drbdmanage cluster is allocated for each
        volume.

        @return: next free minor number; or -1 on error
        """
        try:
            min_nr = int(self._conf[self.KEY_MIN_MINOR_NR])
            minor_list = []
            for resource in self._resources.itervalues():
                for vol in resource.iterate_volumes():
                    minor_obj = vol.get_minor()
                    nr_item = minor_obj.get_value()
                    if nr_item >= min_nr and nr_item <= MinorNr.MINOR_NR_MAX:
                        minor_list.append(nr_item)
            minor_nr = get_free_number(min_nr, MinorNr.MINOR_NR_MAX,
                                       minor_list)
            if minor_nr == -1:
                raise ValueError
        except ValueError:
            minor_nr = MinorNr.MINOR_NR_ERROR
        return minor_nr


    def get_free_port_nr(self):
        """
        Retrieves a free (unused) network port number

        Port numbers are allocated in the range of the configuration values
        KEY_MIN_PORT_NR..KEY_MAX_PORT_NR. A port number that is unique
        across the drbdmanage cluster is allocated for each resource.

        @return: next free network port number; or -1 on error
        """
        try:
            min_nr    = int(self._conf[self.KEY_MIN_PORT_NR])
            max_nr    = int(self._conf[self.KEY_MAX_PORT_NR])

            port_list = []
            for resource in self._resources.itervalues():
                nr_item = resource.get_port()
                if nr_item >= min_nr and nr_item <= max_nr:
                    port_list.append(nr_item)
            port = get_free_number(min_nr, max_nr, port_list)
            if port == -1:
                raise ValueError
        except ValueError:
            port = RES_PORT_NR_ERROR
        return port


    def get_free_node_id(self, resource):
        """
        Retrieves a free (unused) node id number

        Node IDs range from 0 to the configuration value of KEY_MAX_NODE_ID
        and are allocated per resource (the node IDs of the same nodes can
        differ from one assigned resource to another)

        @return: next free node id number; or -1 on error
        """
        try:
            max_node_id = int(self._conf[self.KEY_MAX_NODE_ID])
            id_list = []
            for assg in resource.iterate_assignments():
                id_item = assg.get_node_id()
                if id_item >= 0 and id_item <= int(max_node_id):
                    id_list.append(id_item)
            node_id = get_free_number(0, int(max_node_id), id_list)
        except ValueError:
            node_id = -1
        return node_id


    def get_free_drbdctrl_node_id(self):
        """
        Retrieves a free (unused) node id number

        Node IDs range from 0 to the configuration value of KEY_MAX_NODE_ID
        and are allocated per resource (the node IDs of the same nodes can
        differ from one assigned resource to another)

        @return: next free node id number; or -1 on error
        """
        try:
            max_node_id = int(self._conf[self.KEY_MAX_NODE_ID])

            id_list = []
            for node in self._nodes.itervalues():
                id_item = node.get_node_id()
                if id_item >= 0 and id_item <= max_node_id:
                    id_list.append(id_item)
            node_id = get_free_number(0, max_node_id, id_list)
        except ValueError:
            node_id = -1
        return node_id


    def get_free_volume_id(self, resource):
        """
        Retrieves a free (unused) volume id number

        Volume IDs range from 0 to MAX_RES_VOLS and are allocated per resource

        @return: next free volume id number; or -1 on error
        """
        id_list = []
        for vol in resource.iterate_volumes():
            id_item = vol.get_id()
            if id_item >= 0 and id_item <= DrbdResource.MAX_RES_VOLS:
                id_list.append(id_item)
        vol_id = get_free_number(0, DrbdResource.MAX_RES_VOLS, id_list)
        return vol_id


    @staticmethod
    def catch_internal_error(exc):
        # http://stackoverflow.com/questions/5736080/
        # sys-exc-info1-type-and-format-in-python-2-71
        #
        # (obviously, you have to remove the newline from the link above)
        expl = "Internal error (error traceback failed)"
        args = {}
        try:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            exc_text = (traceback.format_exception_only(exc_type, exc_obj))[0]
            tb = traceback.extract_tb(exc_tb, 3)
            # Everything passed as string, to make dbus happy
            args =  {
                "file1": tb[0][0],
                "line1": str(tb[0][1]),
                'exc':   exc_text.strip()
            }
            expl = "Internal error: In %(file1)s@%(line1)s: %(exc)s"
            if len(tb) > 1:
                args["file2"] = tb[1][0]
                args["line2"] = str(tb[1][1])
                expl += "; called from %(file2)s@%(line2)s"
            logging.critical(expl % args)
            logging.debug("--- start stack trace")
            for tb_entry in traceback.format_tb(exc_tb):
                logging.debug(tb_entry)
            logging.debug("--- end stack trace")
        except Exception:
            pass
        return (expl, args)


    @staticmethod
    def catch_and_append_internal_error(fn_rc, exc):
        msg, args = DrbdManageServer.catch_internal_error(exc)
        add_rc_entry(fn_rc, DM_DEBUG, msg, *args.items())


"""
Tracing - may be used for debugging
"""
def traceit(frame, event, arg):
    if event == "line":
        lineno = frame.f_lineno
        print(frame.f_code.co_filename, ":", "line", lineno)
    return traceit

"""
Uncomment the statement below to enable tracing
"""
#sys.settrace(traceit)
