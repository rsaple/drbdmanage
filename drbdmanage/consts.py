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

"""
Global constants for drbdmanage
"""

NODE_ADDR           = "addr"
NODE_AF             = "addrfam"
NODE_ID             = "node_id"
RES_PORT            = "port"
RES_SECRET          = "secret"
VOL_MINOR           = "minor"

DRBDCTRL_DEFAULT_PORT = 6999

DRBDCTRL_RES_NAME   = ".drbdctrl"
DRBDCTRL_DEV        = "/dev/drbd0"
DEFAULT_VG          = "drbdpool"
