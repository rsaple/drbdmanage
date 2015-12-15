#!/usr/bin/env python2
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

import logging
import dbus
import dbus.service
import dbus.mainloop.glib


class DBusServer(dbus.service.Object):

    """
    dbus API to the drbdmanage server API
    """

    DBUS_DRBDMANAGED = "org.drbd.drbdmanaged"
    DBUS_SERVICE     = "/interface"

    _dbus   = None
    _server = None


    def __init__(self, server):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._dbus = dbus.service.BusName(
            self.DBUS_DRBDMANAGED,
            bus=dbus.SystemBus()
        )
        dbus.service.Object.__init__(self, self._dbus, self.DBUS_SERVICE)
        self._server = server


    def run(self):
        """
        Calls the run() function of the server. It is recommended to call
        that function directly instead of going through the dbus interface.
        """
        self._server.run()


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="",
        out_signature="a(isa(ss))"
    )
    def poke(self):
        """
        D-Bus interface for DrbdManageServer.poke(...)
        """
        return self._server.poke()


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="sa{ss}",
        out_signature="a(isa(ss))"
    )
    def create_node(self, node_name, props):
        """
        D-Bus interface for DrbdManageServer.create_node(...)
        """
        return self._server.create_node(node_name, dict(props))


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="sb",
        out_signature="a(isa(ss))"
    )
    def remove_node(self, node_name, force):
        """
        D-Bus interface for DrbdManageServer.remove_node(...)
        """
        return self._server.remove_node(node_name, force)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="sa{ss}",
        out_signature="a(isa(ss))"
    )
    def create_resource(self, res_name, props):
        """
        D-Bus interface for DrbdManageServer.create_resource(...)
        """
        return self._server.create_resource(res_name, dict(props))


    @dbus.service.method(DBUS_DRBDMANAGED,
      in_signature="sta{ss}", out_signature="a(isa(ss))")
    def modify_resource(self, res_name, serial, props):
        """
        D-Bus interface for DrbdManageServer.modify_resource(...)
        """
        return self._server.modify_resource(res_name, serial, dict(props))


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="sita{ss}",
        out_signature="a(isa(ss))"
    )
    def modify_volume(self, res_name, vol_id, serial, props):
        """
        D-Bus interface for DrbdManageServer.modify_volume(...)
        """
        return self._server.modify_volume(
            res_name, vol_id, serial, dict(props)
        )


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="sittt",
        out_signature="a(isa(ss))"
    )
    def resize_volume(self, res_name, vol_id, serial, size_kiB, delta_kiB):
        """
        D-Bus interface for DrbdManageServer.resize_volume(...)
        """
        return self._server.resize_volume(
            res_name, vol_id, serial, size_kiB, delta_kiB
        )


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="sb",
        out_signature="a(isa(ss))"
    )
    def remove_resource(self, res_name, force):
        """
        D-Bus interface for DrbdManageServer.remove_resource(...)
        """
        return self._server.remove_resource(res_name, force)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="sxa{ss}",
        out_signature="a(isa(ss))"
    )
    def create_volume(self, res_name, size_kiB, props):
        """
        D-Bus interface for DrbdManageServer.create_volume(...)
        """
        return self._server.create_volume(res_name, size_kiB, dict(props))


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="sib",
        out_signature="a(isa(ss))"
    )
    def remove_volume(self, res_name, vol_id, force):
        """
        D-Bus interface for DrbdManageServer.remove_volume(...)
        """
        return self._server.remove_volume(res_name, vol_id, force)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="ssb",
        out_signature="a(isa(ss))"
    )
    def connect(self, node_name, res_name, reconnect):
        """
        D-Bus interface for DrbdManageServer.connect(...)
        """
        return self._server.connect(node_name, res_name, reconnect)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="ssb",
        out_signature="a(isa(ss))"
    )
    def disconnect(self, node_name, res_name, force):
        """
        D-Bus interface for DrbdManageServer.disconnect(...)
        """
        return self._server.disconnect(node_name, res_name, force)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="ssa{ss}",
        out_signature="a(isa(ss))"
    )
    def modify_assignment(self, node_name, res_name, props):
        """
        D-Bus interface for DrbdManageServer.modify_state(...)
        """
        return self._server.modify_state(node_name, res_name, dict(props))


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="ssi",
        out_signature="a(isa(ss))"
    )
    def attach(self, node_name, res_name, vol_id):
        """
        D-Bus interface for DrbdManageServer.attach(...)
        """
        return self._server.attach(node_name, res_name, vol_id)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="ssi",
        out_signature="a(isa(ss))"
    )
    def detach(self, node_name, res_name, vol_id):
        """
        D-Bus interface for DrbdManageServer.detach(...)
        """
        return self._server.detach(node_name, res_name, vol_id)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="ssa{ss}",
        out_signature="a(isa(ss))"
    )
    def assign(self, node_name, res_name, props):
        """
        D-Bus interface for DrbdManageServer.assign(...)
        """
        return self._server.assign(node_name, res_name, dict(props))


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="ssb",
        out_signature="a(isa(ss))"
    )
    def unassign(self, node_name, res_name, force):
        """
        D-Bus interface for DrbdManageServer.unassign(...)
        """
        return self._server.unassign(node_name, res_name, force)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="i",
        out_signature="a(isa(ss))" "xx"
    )
    def cluster_free_query(self, redundancy):
        """
        D-Bus interface for DrbdManageServer.cluster_free_query(...)
        """
        return self._server.cluster_free_query(redundancy)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="siib",
        out_signature="a(isa(ss))"
    )
    def auto_deploy(self, res_name, count, delta, site_clients):
        """
        D-Bus interface for DrbdManageServer.auto_deploy(...)
        """
        return self._server.auto_deploy(res_name, int(count), int(delta), site_clients)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="sb",
        out_signature="a(isa(ss))"
    )
    def auto_undeploy(self, res_name, force):
        """
        D-Bus interface for DrbdManageServer.auto_undeploy(...)
        """
        return self._server.auto_undeploy(res_name, force)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="",
        out_signature="a(isa(ss))"
    )
    def update_pool_check(self):
        """
        D-Bus interface for DrbdManageServer.update_pool_check()
        """
        return self._server.update_pool_check()


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="as",
        out_signature="a(isa(ss))"
    )
    def update_pool(self, node_names):
        """
        D-Bus interface for DrbdManageServer.update_pool(...)
        """
        return self._server.update_pool(node_names)

    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="a{ss}",
        out_signature="a(isa(ss))"
    )
    def set_drbdsetup_props(self, props):
        return self._server.set_drbdsetup_props(dict(props))

    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="asta{ss}as",
        out_signature="a(isa(ss))" "a(sa{ss})"
    )
    def list_nodes(self, node_names, serial, filter_props, req_props):
        """
        D-Bus interface for DrbdManageServer.list_nodes(...)
        """
        return self._server.list_nodes(
            node_names, serial, filter_props, req_props
        )

    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="",
        out_signature="a(isa(ss))" "a{ss}"
    )
    def get_config_keys(self):
        return self._server.get_config_keys()

    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="",
        out_signature="a(isa(ss))" "aa{ss}"
    )
    def get_plugin_default_config(self):
        return self._server.get_plugin_default_config()

    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="",
        out_signature="a(isa(ss))" "a{ss}"
    )
    def get_cluster_config(self):
        return self._server.get_cluster_config()

    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="as",
        out_signature="a(isa(ss))" "a{ss}"
    )
    def get_selected_config_values(self, keys):
        return self._server.get_selected_config_values(keys)

    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="",
        out_signature="a(isa(ss))" "aa{ss}"
    )
    def get_site_config(self):
        return self._server.get_site_config()

    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="a(s(a{ss}))",
        out_signature="a(isa(ss))"
    )
    def set_cluster_config(self, cfgdict):
        return self._server.set_cluster_config(dict(cfgdict))

    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="asta{ss}as",
        out_signature="a(isa(ss))" "a(sa{ss})"
    )
    def list_resources(self, res_names, serial, filter_props, req_props):
        """
        D-Bus interface for DrbdManageServer.list_resources(...)
        """
        return self._server.list_resources(
            res_names, serial, dict(filter_props), req_props
        )


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="asta{ss}as",
        out_signature="a(isa(ss))" "a(sa{ss}a(ia{ss}))"
    )
    def list_volumes(self, res_names, serial, filter_props, req_props):
        """
        D-Bus interface for DrbdManageServer.list_volumes(...)
        """
        return self._server.list_volumes(
            res_names, serial, dict(filter_props), req_props
        )


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="asasta{ss}as",
        out_signature="a(isa(ss))" "a(ssa{ss}a(ia{ss}))"
    )
    def list_assignments(self, node_names, res_names,
                         serial, filter_props, req_props):
        """
        D-Bus interface for DrbdManageServer.list_assignments(...)
        """
        return self._server.list_assignments(
            node_names, res_names, serial, dict(filter_props), req_props
        )


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="ssasa{ss}",
        out_signature="a(isa(ss))"
    )
    def create_snapshot(self, res_name, snaps_name, node_names, props):
        """
        D-Bus interface for DrbdManageServer.create_snapshot(...)
        """
        return self._server.create_snapshot(
            res_name, snaps_name, node_names, dict(props)
        )


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="asasta{ss}as",
        out_signature="a(isa(ss))" "a(sa(sa{ss}))"
    )
    def list_snapshots(self, res_names, snaps_names, serial,
                       filter_props, req_props):
        """
        D-Bus interface for DrbdManageServer.list_snapshots(...)
        """
        return self._server.list_snapshots(
            res_names, snaps_names, serial, dict(filter_props), req_props
        )


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="asasasta{ss}as",
        out_signature="a(isa(ss))" "a(ssa(sa{ss}))"
    )
    def list_snapshot_assignments(self, res_names, snaps_names, node_names,
                                  serial, filter_props, req_props):
        """
        D-Bus interface for DrbdManageServer.list_snapshot_assignments(...)
        """
        return self._server.list_snapshot_assignments(
            res_names, snaps_names, node_names, serial,
            dict(filter_props), req_props
        )


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="sssa(ss)a(ia(ss))",
        out_signature="a(isa(ss))"
    )
    def restore_snapshot(self, res_name, snaps_res_name, snaps_name,
                         res_props, vols_props):
        """
        D-Bus interface for DrbdManageServer.restore_snapshot(...)
        """
        return self._server.restore_snapshot(res_name, snaps_res_name,
                                             snaps_name, res_props, vols_props)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="sssb",
        out_signature="a(isa(ss))"
    )
    def remove_snapshot_assignment(self, res_name, snaps_name, node_name,
                                   force):
        """
        D-Bus interface for DrbdManageServer.remove_snapshot_assignment(...)
        """
        return self._server.remove_snapshot_assignment(
            res_name, snaps_name, node_name, force
        )


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="ssb",
        out_signature="a(isa(ss))"
    )
    def remove_snapshot(self, res_name, snaps_name, force):
        """
        D-Bus interface for DrbdManageServer.remove_snapshot(...)
        """
        return self._server.remove_snapshot(res_name, snaps_name, force)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="ss",
        out_signature="a(isa(ss))" "a{ss}"
    )
    def query_snapshot(self, res_name, snaps_name):
        """
        D-Bus interface for DrbdManageServer.query_snapshot(...)
        """
        return self._server.query_snapshot(res_name, snaps_name)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="ss",
        out_signature="a(isa(ss))"
    )
    def resume(self, node_name, res_name):
        """
        Clear the fail count of a resource's assignments
        """
        return self._server.resume(node_name, res_name)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="",
        out_signature="a(isa(ss))"
    )
    def resume_all(self):
        """
        Clear the fail count of a resource's assignments
        """
        return self._server.resume_all()


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="s",
        out_signature="a(isa(ss))"
    )
    def export_conf(self, res_name):
        """
        D-Bus interface for DrbdManageServer.export_conf(...)
        """
        return self._server.export_conf(res_name)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="sa{ss}b",
        out_signature="a(isa(ss))"
    )
    def quorum_control(self, node_name, props, override_quorum):
        """
        D-Bus interface for DrbdManageServer.quorum_control(...)
        """
        return self._server.quorum_control(node_name, props, override_quorum)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="",
        out_signature="a(isa(ss))"
    )
    def reconfigure(self):
        """
        D-Bus interface for DrbdManageServer.reconfigure()
        """
        return self._server.reconfigure()


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="as",
        out_signature="a(isa(ss))" "as"
    )
    def text_query(self, command):
        """
        D-Bus interface for DrbdManageServer.text_query(...):
        """
        return self._server.text_query(command)


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="sa{ss}",
        out_signature="a(isa(ss))"
    )
    def init_node(self, node_name, props):
        """
        D-Bus interface for DrbdManageServer.init_node(...)
        """
        return self._server.init_node(node_name, props)

    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="a{ss}",
        out_signature="a(isa(ss))"
    )
    def assign_satellite(self, props):
        return self._server.assign_satellite(props)

    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="a{ss}",
        out_signature="a(isa(ss))"
    )
    def join_node(self, props):
        """
        D-Bus interface for DrbdManageServer.join_node(...)
        """
        return self._server.join_node(props)

    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="sa{ss}",
        out_signature="a(isa(ss))" "a{ss}"
    )
    def run_external_plugin(self, plugin_name, props):
        """
        D-Bus interface for DrbdManageServer.create_node(...)
        """
        return self._server.run_external_plugin(plugin_name, dict(props))


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="",
        out_signature="a(isa(ss))"
    )
    def load_conf(self):
        """
        D-Bus interface for DrbdManageServer.load_conf()
        """
        return self._server.dbus_load_conf()


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="",
        out_signature="a(isa(ss))"
    )
    def save_conf(self):
        """
        D-Bus interface for DrbdManageServer.save_conf()
        """
        return self._server.dbus_save_conf()


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="",
        out_signature="i"
    )
    def ping(self):
        """
        D-Bus ping/pong connection test

        This function can be used to test whether a client can communicate
        with the D-Bus interface of the drbdmanage server.
        If D-Bus service activation is configured, this function can also be
        used to start the drbdmanage server.
        """
        return 0


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="a{ss}",
        out_signature=""
    )
    def shutdown(self, props):
        """
        D-Bus interface for DrbdManageServer.shutdown()
        """
        logging.info("server shutdown requested through D-Bus")
        self._server.shutdown(dict(props))


    @dbus.service.method(
        DBUS_DRBDMANAGED,
        in_signature="s",
        out_signature="i"
    )
    def debug_console(self, command):
        """
        D-Bus interface for DrbdManageServer.debug_console(...)
        """
        return self._server.debug_console(command)


class DBusSignal(dbus.service.Object):
    PATH_PREFIX = "/objects"

    _path = None


    def __init__(self, path):
        if len(path) >= 1:
            if path[0] == "/":
                self._path = DBusSignal.PATH_PREFIX + path
            else:
                self._path = DBusSignal.PATH_PREFIX + "/" + path
        if self._path is not None:
            dbus.service.Object.__init__(self, dbus.SystemBus(), self._path)
            logging.debug("DBusSignal '%s': Instance created" % self._path)
        else:
            logging.debug("DBusSignal: Dummy instance created (no valid path specified)")


    @dbus.service.signal(DBusServer.DBUS_DRBDMANAGED)
    def notify_changed(self):
        """
        Signal to notify subscribers of a change

        This signal is to be sent to notify subscribers that the state of
        the object this signal is associated with has changed
        """
        logging.debug("DBusSignal '%s': notify_changed()" % self._path)


    @dbus.service.signal(DBusServer.DBUS_DRBDMANAGED)
    def notify_removed(self):
        """
        Signal to notify subscribers to unsubscribe

        This signal is to be sent whenever an instance of this class
        is removed (e.g., the server withdraws the DBus registration
        of the object associated with this signal and will therefore
        discard the DBusSignal instance, too)
        """
        logging.debug("DBusSignal '%s': notify_removed()" % self._path)


    def destroy(self):
        """
        Withdraws this instance from the DBus interface
        """
        # Notify any subscribers of the removal of this DBus service object
        self.notify_removed()
        # Remove the DBus service object
        if self._path is not None:
            self.remove_from_connection()


class DBusSignalFactory():
    """
    Instance factory for the DBusSignal class

    An object of this class is passed to the drbdmanage server to enable
    it to create instances of the DBusSignal class, so the drbdmanage
    server can be kept independent of the DBus layer.
    """

    def __init__(self):
        pass


    def create_signal(self, path):
        return DBusSignal(path)

