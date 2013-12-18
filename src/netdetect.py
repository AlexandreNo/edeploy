#!/usr/bin/env python
#
# Copyright (C) 2013 eNovance SAS <licensing@enovance.com>
#
# Author: Erwan Velu <erwan.velu@enovance.com>
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import collections
from commands import getstatusoutput as cmd
import cPickle
import netaddr
import numpy
import os
import pprint
import re
import socket
import struct
import subprocess
import sys
import threading
import time

timestamp = {}
server_list = {}
semaphore = threading.Semaphore()
synthesis = False
discovery = True
leader = False
ready_to_bench = True
my_mac_addr = ''
hw = []
wait_go = None
max_clients = 0
bw_results = []
bw_results_semaphore = threading.Semaphore()
selected_subnet = ''

''' How many seconds between sending a keep alive message '''
KEEP_ALIVE = 2

''' Amount of seconds no system shall {dis]appear '''
DISCOVERY_TIME = 20 * KEEP_ALIVE

''' Multicast Address used to communicate '''
MCAST_GRP = '224.1.1.1'

''' Mutlicast Port used to communicate '''
MCAST_PORT = 10987
MCAST_PORT_GO = 10988

BENCH_PORT_BASE = 10000

BENCH_DURATION = 30


def get_mac(hw, level1, level2):
    '''Extract a Mac Address from an hw list.'''
    for entry in hw:
        if (level1 == entry[0] and level2 == entry[2]):
            return entry[3]
    return None


def get_value(hw, level1, level2, level3):
    for entry in hw:
        if (level1 == entry[0] and level2 == entry[1] and level3 == entry[2]):
            return entry[3]
    return None


def get_cidr_from_eth(hw, eth):
    if (eth):
        for entry in hw:
            if (entry[0] == 'network') and \
               (entry[1] == eth) and (entry[2] == 'ipv4-cidr'):
                return entry[3]


def get_network_from_eth(hw, eth):
    if (eth):
        for entry in hw:
            if (entry[0] == 'network') and \
               (entry[1] == eth) and (entry[2] == 'ipv4-network'):
                return entry[3]


def get_ip_list(hw):
    '''Extract All IPV4 addresses from hw list.'''
    ip_list = []
    for entry in hw:
        if (entry[0] == 'network') and (entry[2] == 'ipv4'):
            ip_list.append('%s/%s/%s' %
                           (entry[3],
                            get_cidr_from_eth(hw, entry[1]),
                            get_network_from_eth(hw, entry[1])))
    return ip_list


def start_sync_bench_server():
    '''Server is made for receiving keepalives and manage them.'''
    global server_list
    ''' Let's bind a server to the Multicast group '''
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((MCAST_GRP, MCAST_PORT))
    mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    servers_ready_to_bench = {}
    while len(servers_ready_to_bench) != len(server_list):
        answer = {}
        ''' Let's get keepalives from servers '''
        answer = cPickle.loads(sock.recv(10240))
        ''' If the keepalive is Synthesis, we shall only consider this list '''
        if 'READY_TO_BENCH' in answer.keys():
            if answer['READY_TO_BENCH'] not in servers_ready_to_bench.keys():
                servers_ready_to_bench[answer['READY_TO_BENCH']] = True
            sys.stderr.write("Received Ready_to_bench from %s\n" %
                             answer['READY_TO_BENCH'])

    sys.stderr.write('All servers are now ready to bench\n')


def start_discovery_server():
    '''Server is made for receiving keepalives and manage them.'''
    global server_list
    global timestamp
    global synthesis
    global discovery
    global max_clients
    global selected_subnet

    ''' Let's bind a server to the Multicast group '''
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((MCAST_GRP, MCAST_PORT))
    mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    if (max_clients > 0):
        sys.stderr.write("Waiting endlessly for %d clients\n" % max_clients)

    ''' Until we got a synthesis list from another server '''
    while not synthesis:
        answer = {}
        ''' Let's get keepalives from servers '''
        answer = cPickle.loads(sock.recv(10240))

        ''' Let's protect shared variables with the scrubbing '''
        semaphore.acquire()

        ''' One client got a max_client information, let's use the same '''
        if ('MAX_CLIENTS' in answer.keys()) and (max_clients == 0):
            max_clients = int(answer['MAX_CLIENTS'])
            sys.stderr.write("Received max_clients = %d\n" % max_clients)

        ''' If no clients got detected on time, let's kill the server '''
        if 'NOTHING_TO_DO' in answer.keys():
            sys.stderr.write("Nothing to do, let's exit server\n")
            semaphore.release()
            break

        ''' If the keepalive is Synthesis, we shall only consider this list '''
        if 'SYNTHESIS' in answer.keys():
            sys.stderr.write("Received Synthesis\n")
            server_list = collections.OrderedDict()
            timestamp = {}
            synthesis = True
            # We are no more in a discovery phase, that will kill client '''
            discovery = False

            # Let's save the selected subnet for later reporting '''
            selected_subnet = answer['subnet']
            del(answer['subnet'])

            # Remove Synthesis entry as it doesn't mean a server '''
            del(answer['SYNTHESIS'])

            # We shall wait at least 2 Keep alives to ensure client
            # completed his task.
            time.sleep(2 * KEEP_ALIVE)

        for key in answer.keys():
            if key not in server_list.keys():
                if key == 'MAX_CLIENTS':
                    continue
                ''' Let's add the new server if we didn't knew it '''
                if not synthesis:
                    sys.stderr.write('Adding %s\n' % key)
                server_list[key] = answer[key]
                timestamp[key] = time.time()
            else:
                ''' We knew that server, let's refresh the timestamp '''
                sys.stderr.write('Received Keepalive from %s\n' % key)
                timestamp[key] = time.time()

        ''' No more concurrency with scrubbing, let's release the semaphore '''
        semaphore.release()

        if (max_clients > 0):
            sys.stderr.write("Found %d of %d servers\n" %
                             (len(server_list), max_clients))

    sys.stderr.write("Exiting server\n")


def start_client(mode, max_clients=0):
    '''Client is made for generating keepalives.'''
    global hw
    global discovery
    global ready_to_bench
    global my_mac_addr

    ''' Let's prepare the socket '''
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

    if mode == 'DISCOVERY':
        ''' Let's find all IPV4 addresses we know about the system '''
        my_ip_list = get_ip_list(hw)

        ''' Let's find our mac address '''
        my_mac_addr = '%s' % get_mac(hw, 'network', 'serial')

        ''' Let's prepare a host entry '''
        host_info = {}
        host_info[my_mac_addr] = my_ip_list

        ''' Let's inform other users we had a max_client setting '''
        if (max_clients > 0):
            host_info['MAX_CLIENTS'] = max_clients

        ''' While we are in discovery mode, let's send keepalives '''
        while discovery:
            sys.stderr.write("Sending keepalive for %s\n" % my_mac_addr)
            sock.sendto(cPickle.dumps(host_info), (MCAST_GRP, MCAST_PORT))
            time.sleep(KEEP_ALIVE)
        sys.stderr.write("Exiting Client, end of discovery\n")

    elif mode == 'READY_TO_BENCH':
        host_info = {}
        host_info['READY_TO_BENCH'] = my_mac_addr
        ready_to_bench = True
        while ready_to_bench:
            sys.stderr.write("Sending Ready To Bench for %s\n" % my_mac_addr)
            sock.sendto(cPickle.dumps(host_info), (MCAST_GRP, MCAST_PORT))
            time.sleep(KEEP_ALIVE)
        #sys.stderr.write("Exiting Client, end of ready to bench\n")
    elif mode == 'GO':
        host_info = {}
        host_info['GO'] = my_mac_addr
        sys.stderr.write("Sending Go !\n")
        sock.sendto(cPickle.dumps(host_info), (MCAST_GRP, MCAST_PORT_GO))
    else:
        sys.stderr.write("start_client: Invalide mode : %s\n" % mode)
        return


def fatal_error(error):
    '''Report a shell script with the error message and log
       the message on stderr.
    '''
    sys.stderr.write('%s\n' % error)
    sys.exit(1)


def prepare_synthesis():
    global server_list
    network_list = {}
    new_server_list = {}
    for server in server_list.keys():
        netaddrs = server_list[server]
        for net in netaddrs:
            network = "%s/%s" % (net.split('/')[2], net.split('/')[1])
            if network not in network_list.keys():
                network_list[network] = 1
            else:
                network_list[network] += 1

    count = 0
    selected_network = ''
    for key in network_list.keys():
        if network_list[key] > count:
            count = network_list[key]
            selected_network = key

    sys.stderr.write("Selected network is %s\n" % (selected_network))

    valid_ip_list = [
        str(ip) for ip in netaddr.IPNetwork(selected_network).iter_hosts()]

    for server in server_list.keys():
        netaddrs = server_list[server]
        for net in netaddrs:
            remote_ip = net.split('/')[0]
            if remote_ip in valid_ip_list:
                new_server_list[server] = remote_ip

    server_list = new_server_list
    server_list['subnet'] = selected_network
    server_list['SYNTHESIS'] = True


def scrub_timestamp():
    '''Scrubing deletes server that didn't sent keepalive on time.'''
    global discovery
    global timestamp
    global server_list
    global leader
    previous_system_count = 0
    previous_time = time.time()
    system_count = 0

    ''' Scurbbing have only a meaning during discovery '''
    while discovery:
        current_time = time.time()
        semaphore.acquire()

        ''' Let's check all entries we knew '''
        for key in timestamp.keys():
            ''' If we missed two consecutive keep alive,'''
            if current_time > timestamp[key] + (KEEP_ALIVE * 3):
                ''' We need to delete this server '''
                sys.stderr.write('Deleting too old entry : %s\n' % key)
                del timestamp[key]
                del server_list[key]
        #sys.stderr.write("%d systems detected\n"%len(timestamp.keys()))
        system_count = len(timestamp.keys())
        semaphore.release()

        ''' If the number of known systems changed + or -'''
        ''' let's save the current time & amount of servers '''
        if (system_count != previous_system_count):
            previous_system_count = system_count
            previous_time = current_time
        else:
            ''' Let's wait until we didn't got any changes
                since DISCOVERY_TIME seconds or
                if we reach the expect number of clients'''
            if (max_clients > 0):
                if (system_count == max_clients):
                    ''' We are no more in discovery '''
                    '''that will kill the client '''
                    discovery = False
            elif current_time - previous_time >= DISCOVERY_TIME:
                    ''' We are no more in discovery, '''
                    '''that will kill the client '''
                    discovery = False

            if discovery is False:
                sock = socket.socket(
                    socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)

                if system_count == 0:
                    sys.stderr.write("No system detected, exiting\n")
                    message = {}
                    message['NOTHING_TO_DO'] = True
                    sock.sendto(cPickle.dumps(server_list),
                                (MCAST_GRP, MCAST_PORT))
                    return
                if system_count == 1:
                    message = {}
                    message['NOTHING_TO_DO'] = True
                    sys.stderr.write("No remote system detected, exiting\n")
                    sock.sendto(cPickle.dumps(message),
                                (MCAST_GRP, MCAST_PORT))
                    return
                sys.stderr.write("About to send the synthesis\n")

                prepare_synthesis()

                ''' We need to wait two keep alive to '''
                '''insure client completed his task '''
                time.sleep(2 * KEEP_ALIVE)

                ''' If a synthesis got received in between, '''
                '''there is no more thing to do '''
                if synthesis:
                    sys.stderr.write(
                        "Received synthesis in between, aborting our\n")
                    return

                ''' It's time to send the synthesis to the other nodes '''
                leader = True
                sock.sendto(cPickle.dumps(server_list),
                            (MCAST_GRP, MCAST_PORT))
                sys.stderr.write("End of Discovery with %d systems!\n" %
                                 system_count)
                return
        ''' Let's wait a KEEP_ALIVE seconds before scrubbing again '''
        time.sleep(KEEP_ALIVE)


def print_result():
    global server_list
    sys.stderr.write("Synthesis result\n")
    for key in server_list:
        sys.stderr.write("Server %s -> " % key)
        sys.stderr.write("%s " % server_list[key])
        sys.stderr.write("\n")


def get_port_list():
    global server_list
    port_list = []
    for server_count in range(len(server_list)):
        if server_count == server_list.keys().index(my_mac_addr):
            continue
        port_list.append(BENCH_PORT_BASE + server_count)
    return port_list


def start_bench_server(port):
    sys.stderr.write('Spawning netserver on port %d\n' % port)
    status, output = cmd('netserver -p %d' % port)


def spawn_bench_servers(port_list):
    threads = {}
    for port in port_list:
        threads[port] = threading.Thread(
            target=start_bench_server, args=tuple([port]))
        threads[port].start()


def stop_bench_servers():
    cmd('pkill -9 netserver')


def start_bench_client(ip, port):
    sys.stderr.write("Starting bench client on server %s:%s\n" % (ip, port))
    cmd_netperf = subprocess.Popen(
        'netperf -l %d -H %s -p %s -t TCP_STREAM' % (int(BENCH_DURATION),
                                                     ip, port),
        shell=True, stdout=subprocess.PIPE)

# [root@localhost ~]# netperf -l 10 -H localhost  -f M -t TCP_STREAM
# MIGRATED TCP STREAM TEST from 0.0.0.0 (0.0.0.0) \
# port 0 AF_INET to localhost.localdomain (127.0.0.1)
# port 0 AF_INET
# Recv   Send    Send
# Socket Socket  Message  Elapsed
# Size   Size    Size     Time     Throughput
# bytes  bytes   bytes    secs.    MBytes/sec
#     87380  16384  16384    10.00    4882.51
    for line in cmd_netperf.stdout:
        if "87380" in line:
            (recv_sock_size, send_sock_size,
             send_msg_size, time, bw) = line.rstrip('\n').split()
            bw_results_semaphore.acquire()
            bw_results.append(float(bw))
            bw_results_semaphore.release()


def spawn_bench_client():
    port = BENCH_PORT_BASE + server_list.keys().index(my_mac_addr)
    threads = {}
    nb = 0
    for server in server_list:
        if my_mac_addr in server:
            continue
        threads[nb] = threading.Thread(
            target=start_bench_client, args=[server_list[server], port])
        threads[nb].start()
        nb += 1

    sys.stderr.write('Waiting %d bench clients to finish\n' % nb)
    for i in range(nb):
        threads[i].join()

    arr = numpy.array(bw_results)

    hw.append(('network', 'tcp_bench', 'bw_host', '%s' % numpy.sum(arr)))
    hw.append(('network', 'tcp_bench', 'streams_count', '%d' % nb))
    hw.append(('network', 'tcp_bench', 'bw_stream_min', '%s' %
              numpy.amin(arr)))
    hw.append(('network', 'tcp_bench', 'bw_stream_max', '%s' %
              numpy.amax(arr)))
    hw.append(('network', 'tcp_bench', 'bw_stream_average', '%s' %
              numpy.average(arr)))
    hw.append(('network', 'tcp_bench', 'bw_stream_median', '%s' %
              numpy.median(arr)))
    hw.append(('network', 'tcp_bench', 'bw_stream_stddev', '%s' %
              numpy.std(arr)))
    hw.append(('network', 'tcp_bench', 'bw_stream_raw_values', '%s' %
              bw_results))
    hw.append(('network', 'tcp_bench', 'subnet', '%s' % selected_subnet))
    hw.append(('network', 'tcp_bench', 'hosts_list', '%s' %
              server_list.items()))
    sys.stderr.write('Benchmmark completed !\n')


def wait_for_go():
    global ready_to_bench
    ''' Let's bind a server to the Multicast group '''
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((MCAST_GRP, MCAST_PORT_GO))
    mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    while ready_to_bench:
        answer = {}
        ''' Let's get keepalives from servers '''
        answer = cPickle.loads(sock.recv(10240))
        ''' If the keepalive is Synthesis, we shall only consider this list '''
        if 'GO' in answer.keys():
            sys.stderr.write("Received GO from %s\n" % answer['GO'])
            ready_to_bench = False


def get_output_filename(hw):
    sysname = ''

    sysprodname = get_value(hw, 'system', 'product', 'name')
    if sysprodname:
        sysname = re.sub(r'\W+', '', sysprodname) + '-'

    sysprodvendor = get_value(hw, 'system', 'product', 'vendor')
    if sysprodvendor:
        sysname += re.sub(r'\W+', '', sysprodvendor) + '-'

    sysprodserial = get_value(hw, 'system', 'product', 'serial')
    if sysprodserial:
        sysname += re.sub(r'\W+', '', sysprodserial)

    mac = get_mac(hw, 'network', 'serial')
    if mac:
        sysname += mac.replace(':', '-')

    return sysname + ".perf.hw"


def spawn_bench_synchronize():
    global server_list
    global wait_go
    wait_go = threading.Thread(target=wait_for_go, args=())
    wait_go.start()

    send_ready_to_bench = threading.Thread(
        target=start_client, args=tuple(['READY_TO_BENCH']))
    send_ready_to_bench.start()


def send_bench_start():
    sys.stderr.write("Sending start signal\n")
    start_sync_bench_server()
    start_client('GO')


def _main():
    global hw
    global max_clients

    try:
        max_clients = int(os.environ['MAX_CLIENTS'])
    except Exception:
        max_clients = 0

    hw = eval(open(sys.argv[1]).read(-1))

    ''' Let's start the client '''
    client = threading.Thread(
        target=start_client, args=tuple(['DISCOVERY', max_clients]))
    client.start()

    ''' Let's start scrubbing the server list '''
    scrub = threading.Thread(target=scrub_timestamp)
    scrub.start()

    ''' Let's start the discovery server side '''
    start_discovery_server()

    ''' Let's wait scrub & client completed '''
    scrub.join()
    client.join()

    if (len(server_list) > 1):
        print_result()

        if my_mac_addr not in server_list:
            msg = "Local mac address %s is not part of the final list, let's "
            "exit\n"
            sys.stderr.write(msg % my_mac_addr)
            sys.exit(0)

        sys.stderr.write("I'm server no %d\n" %
                         server_list.keys().index(my_mac_addr))
        if leader:
            sys.stderr.write("I'm also the leader !\n")

        spawn_bench_servers(get_port_list())

        spawn_bench_synchronize()

        if leader:
            send_bench_start()

        wait_go.join()

        spawn_bench_client()

        stop_bench_servers()

    pprint.pprint(hw)

if __name__ == "__main__":
    _main()
