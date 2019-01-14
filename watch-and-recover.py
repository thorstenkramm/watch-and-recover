#!/usr/bin/env python
# -*- coding: utf-8 -*-

import platform
import subprocess
import ConfigParser
import json
import os.path
import re
import sys
import time
import argparse
import pickle
import hashlib


class WatchAndRecover:
    """Main class"""

    __jobs = list()
    __processes = list()
    __state = dict()
    __state_file = str()
    __message_buffer = dict()
    __zabbix_agentd_conf = str()
    __zabbix_sender_bin = str()
    __groups = dict()
    __group_processes = dict()
    __args = object()
    __config_hash = str()
    __cwd = str()

    def __init__(self, args):
        """
        Init of the class
        """
        self.__args = args
        self.__read_config()
        self.__send_job_discovery()
        self.__read_processlist()
        self.__watch()
        self.__write_state()

    def __read_sate_file(self):
        """
        Read the state of the previous run
        :return:
        """
        if os.path.isfile(os.path.expanduser(self.__state_file)):
            with open(os.path.expanduser(self.__state_file)) as f:
                self.__state = json.load(f)
        else:
            # Create default state dict()
            self.__state = {
                'groups': dict(),
                'jobs': dict(),
                'config_hash': '',
                'last_run': 0,
                'last_discovery': 0
            }

    def __write_state(self):
        """
        Write the state to a file
        :return:
        """
        if len(self.__state) > 0:
            self.__state['config_hash'] = self.__config_hash
            self.__state['last_run'] = int(time.time())
            with open(os.path.expanduser(self.__state_file), 'w') as outfile:
                json.dump(self.__state, outfile)
                if '--print-state' in sys.argv[1:]:
                    print "State:"
                    print json.dumps(self.__state, indent=4)

    def __watch(self):
        """
        Look for a process and take action
        :return:
        """
        for job in self.__jobs:
            proc_count = 0;
            for process in self.__processes:
                if int(process['PID']) == int(os.getpid()):
                    continue

                if int(process['PPID']) == int(os.getpid()):
                    continue

                if re.search(job['watch_for'], process['CMD']):
                    proc_count = proc_count + 1

            # Send number of running processes to the Zabbix-Server
            self.__proc_num(job['name'], proc_count)

            if job['group'] is not None:
                # If process belongs to a group, store how many processes of the group are alive
                if job['group'] in self.__group_processes:
                    self.__group_processes[job['group']] = self.__group_processes[job['group']] + proc_count
                else:
                    self.__group_processes[job['group']] = proc_count

            if proc_count == 0:
                self.__message_append(job['name'], "Process \"%s\" encountered dead." % job['watch_for'])
                self.__recover(job)
            else:
                # If job does not belong to a group delete old state
                if job['group'] is None:
                    self.__delete_job_state(job)
                    return None
        # Watch if all processes from a group are running
        for name, group in self.__groups.iteritems():
            if self.__group_processes[name] >= group['members']:
                # All processes of the group are running
                self.__delete_group_state(name)

    def __get_job_state(self, job):
        if job['group'] is not None:
            # Job belongs to a group, state is stored for the group
            if job['group'] in self.__state['groups']:
                self.__message_append(job['name'], "Job \"%s\" belongs to group \"%s\" so group settings have precedence" % (job['name'], job['group']))
                return self.__state['groups'][job['group']]
        else:
            if job['name'] in self.__state['jobs']:
                return self.__state['jobs'][job['name']]
        # Always return default values for non-existing jobs or groups
        return {'tries': 0, 'last_execution': 0}

    def __update_job_state(self, job, state):
        if job['group'] is not None:
            # Job belongs to a group, state is stored for the group
            self.__state['groups'][job['group']] = state
        else:
            self.__state['jobs'][job['name']] = state

    def __delete_job_state(self, job):
        if job['group'] is not None:
            # Job belongs to a group, state is stored for the group
            return None
        else:
            if job['name'] in self.__state['jobs']:
                self.__message_append(job['name'], "Process \"%s\" has come back." % job['watch_for'], send=True)
                del self.__state['jobs'][job['name']]

    def __delete_group_state(self, name):
        if name in self.__state['groups']:
            self.__message_append('global', "All processes of group '%s' are back" % name, send=True)
            del self.__state['groups'][name]

    def __recover(self, job):
        """
        Execute the recovery script
        :return:
        """
        state = self.__get_job_state(job)
        time_lapse = int(round(time.time() - int(state['last_execution']), 0))
        if state['tries'] >= int(job['tries']):
            self.__message_append(job['name'], "Recovery Job executed %s times." % str(state['tries']))
            self.__message_append(job['name'], "Maximum of %s reached." % str(job['tries']), send=True)
            return None

        if time_lapse < int(job['delay']):
            self.__message_append(job['name'], "Recovery executed %s second(s) ago but should wait %s seconds." % (time_lapse, job['delay']), send=True)
            return None

        # Execute the recovery script
        tries = state['tries'] + 1
        self.__message_append(job['name'], "This is try number %s of %s" % (tries, job['tries']))
        self.__update_job_state(job, {'last_execution': time.time(), 'tries': tries})
        cmd = 'nohup ' + job['recover_with']
        cwd = os.path.expanduser(job['cwd'])
        self.__message_append(job['name'], "Executing \"%s\" in \"%s\" now." % (cmd, cwd))
        logfile = '/tmp/' + job['name'] + '-recovery.log'
        self.__message_append(job['name'], "An error log can be found at \"%s\"." % logfile)
        try:
            cmd_return = subprocess.Popen(cmd.split(),
                                          stdout=open('/dev/null', 'w'),
                                          stderr=open(logfile, 'a'),
                                          preexec_fn=os.setpgrp,
                                          cwd=cwd
                                          )
            self.__message_append(job['name'], "Recovery has been executed with PID \"%s\"." % cmd_return.pid, send=True)
        except OSError as error:
            self.__message_append(job['name'], "Recovery failed with error \"%s\"." % error, send=True)

    def __read_config(self):
        """
        Read the config file
        :return:
        """
        self.__args.config = os.path.expanduser(self.__args.config)
        if os.path.isfile(self.__args.config) is False:
            sys.stderr.write("Config file %s not found. Exit!\n" % self.__args.config)
            sys.exit(1)
        config = ConfigParser.RawConfigParser()
        config.read([self.__args.config])
        self.__config_hash = hashlib.sha1(pickle.dumps(config)).hexdigest()
        try:
            self.__state_file = str(config.get('main', 'state_file'))
            self.__read_sate_file()
        except ConfigParser.NoOptionError:
            sys.stderr.write("Config 'state_file' missing. Append it to the [main] section. Exit\n");
            sys.exit(1)

        try:
            self.__zabbix_sender_bin = str(config.get('main', 'zabbix_sender_bin'))
            self.__read_sate_file()
        except ConfigParser.NoOptionError:
            self.__zabbix_sender_bin = None
            self.__info("Config 'zabbix_sender_bin' missing. Working without sending data to Zabbix-Server");

        try:
            self.__zabbix_agentd_conf = str(config.get('main', 'zabbix_agentd_conf'))
            self.__read_sate_file()
        except ConfigParser.NoOptionError:
            sys.stderr.write("Config 'zabbix_agentd_conf' missing. Append it to the [main] section. Exit\n");
            sys.exit(1)

        try:
            self.__cwd = str(config.get('main', 'cwd'))
        except ConfigParser.NoOptionError:
            self.__cwd = '/tmp'

        for section in config.sections():
            # Loop over all sections staring with 'group:'
            if section[0:6] == 'group:':
                group = dict()
                group['name'] = section[6:]
                group['members'] = 0
                try:
                    group['tries'] = str(config.get(section, 'tries'))
                except ConfigParser.NoOptionError:
                    sys.stderr.write("No 'tries' set for group in [group:%s] section. Exit!\n" % group['name'])
                    sys.exit(1)
                try:
                    group['delay'] = str(config.get(section, 'delay'))
                except ConfigParser.NoOptionError:
                    sys.stderr.write("No 'delay' set for group in [group:%s] section. Exit!\n" % group['name'])
                    sys.exit(1)
                try:
                    group['cwd'] = str(config.get(section, 'cwd'))
                except ConfigParser.NoOptionError:
                    group['cwd'] = None
                self.__groups[section[6:]] = group

        for section in config.sections():
            # Loop over all sections staring with 'watch:'
            # aka get the jobs
            if section[0:6] == 'watch:':
                job = dict()
                job['name'] = section[6:]
                job['watch_for'] = str(config.get(section, 'watch_for'))
                job['recover_with'] = str(config.get(section, 'recover_with'))
                try:
                    job['group'] = str(config.get(section, 'group'))
                except ConfigParser.NoOptionError:
                    job['group'] = None

                try:
                    job['cwd'] = config.get(section, 'cwd')
                except ConfigParser.NoOptionError:
                    if job['group'] is not None and self.__groups[job['group']]['cwd'] is not None:
                        job['cwd'] = self.__groups[job['group']]['cwd']
                    else:
                        job['cwd'] = self.__cwd

                if job['group'] is not None:
                    # Check if referenced group exists
                    if job['group'] not in self.__groups:
                        sys.stderr.write("Section [%s] references a non-existing group '%s'. Exit!\n" % (section, job['group']))
                        sys.exit(1)
                    # Update the member counter of the group
                    self.__groups[job['group']]['members'] = self.__groups[job['group']]['members'] + 1

                # Get the tries, how often a recovery job should be executed
                try:
                    job['tries'] = str(config.get(section, 'tries'))
                    if job['group'] is not None:
                        sys.stderr.write("Ambiguous settings in section [%s]. Do not use 'tries' for a job if job belongs to a group. Exit!\n" % section)
                        sys.exit(1)
                except ConfigParser.NoOptionError:
                    if job['group'] is not None:
                        # Get the tries values from the group
                        job['tries'] = self.__groups[job['group']]['tries']
                    else:
                        # Try to get individual tries
                        try:
                            job['tries'] = str(config.get('main', 'tries'))
                        except ConfigParser.NoOptionError:
                            sys.stderr.write("No 'tries' set in [main] section nor in [watch:%s] not for the group section. Exit!\n" % job['name'])
                            sys.exit(1)
                # Get the delay between recovery actions
                try:
                    job['delay'] = str(config.get(section, 'delay'))
                    if job['group'] is not None:
                        sys.stderr.write("Ambiguous settings in section [%s]. Do not use 'delay' for a job if job belongs to a group. Exit!\n" % section)
                        sys.exit(1)
                except ConfigParser.NoOptionError:
                    if job['group'] is not None:
                        # Get the tries values from the group
                        job['delay'] = self.__groups[job['group']]['delay']
                    else:
                        try:
                            job['delay'] = str(config.get('main', 'delay'))
                        except ConfigParser.NoOptionError:
                            sys.stderr.write("No \"delay\" set in [main] section nor in [watch:%s] section. Exit!\n" % job['name'])
                            sys.exit(1)
                self.__jobs.append(job)
        if self.__args.print_jobs is True:
            print "Jobs:"
            print json.dumps(self.__jobs, indent=4)
        if self.__args.print_groups is True:
            print "Groups:"
            print json.dumps(self.__groups, indent=4)

    def __read_processlist(self):
        """
        Get list() of running processes
        :return:
        """
        processes = subprocess.check_output('ps -ef', stderr=subprocess.STDOUT, shell=True).splitlines()
        for process in processes:
            proc = dict()
            parts = process.split()
            proc['UID'] = parts[0]
            proc['PID'] = parts[1]
            proc['PPID'] = parts[2]
            if platform.system() == 'AIX':
                proc['CMD'] = ' '.join(parts[8:])
            else:
                proc['CMD'] = ' '.join(parts[7:])
            if proc['PID'] != 'PID':
                self.__processes.append(proc)

    def __message_append(self, job_name, msg, send=None):
        if job_name not in self.__message_buffer:
            self.__message_buffer[job_name] = list()
        self.__message_buffer[job_name].append(msg)
        if send is True:
            self.__send_messages(job_name)

    def __send_messages(self, job_name):
        """
        Send messages for a job to the Zabbix-Server if zabbix_sender has been configured
        :param job_name:
        :return:
        """
        if job_name in self.__message_buffer:
            if self.__args.verbosity > 0:
                print "Info about job '" + job_name + "'"
                print "\n".join(self.__message_buffer[job_name])
            key = 'proc.recovery.job_message[%s]' % job_name
            self.__zabbix_sender(key, "\n".join(self.__message_buffer[job_name]))

    def __send_job_discovery(self):
        """
        Send a json object so Zabbix can create items for each job.
        Send only on config changes and every 3600 seconds
        :return:
        """
        if (self.__config_hash == self.__state['config_hash'] and
                (int(time.time()) - self.__state['last_discovery'] < 3600) and
                self.__args.print_discovery is not True):
            self.__info("Config has not changed. Skipping discovery.", 2)
            return None

        self.__state['last_discovery'] = int(time.time())
        zbx_data = dict()
        zbx_data['data'] = list()
        for job in self.__jobs:
            zbx_data['data'].append({'{#JOB_NAME}': job['name'], '{#WATCH_FOR}': job['watch_for'], '{#RECOVER_WITH}': job['recover_with']})
        self.__zabbix_sender('proc.recovery.jobs', json.dumps(zbx_data))
        if self.__args.print_discovery is True:
            print json.dumps(zbx_data, indent=4)

    def __zabbix_sender(self, key, value):
        if self.__zabbix_sender_bin is None:
            return None
        sender_cmd = self.__zabbix_sender_bin + ' -c ' + self.__zabbix_agentd_conf + ' -k ' + key + " -o '" + value + "'"
        try:
            sender_result = subprocess.check_output(sender_cmd, stderr=subprocess.STDOUT, shell=True)
            if 'processed: 1; failed: 0' not in sender_result:
                sys.stderr.write("zabbix_sender failed. Got %s\n" % sender_result)
            self.__info("Executed:\n\"%s\"\nwith result:\n%s" % (sender_cmd, sender_result), 2)
        except subprocess.CalledProcessError as error:
            sys.stderr.write("zabbix_sender failed with %s\n" % error)

    def __proc_num(self, proc, num):
        """
        Send the number of running processes to the Zabbix-Server
        :param proc:
        :param num:
        :return:
        """
        if self.__state['last_run'] == 0:
            # Don't send individual item values on the first run,
            # because the Zabbix-Server needs some time to process the discovery list to create the items
            self.__info("Skipped sending item values on first run.", 2)
            return None
        key = 'trapper.proc.num[%s]' % str(proc)
        self.__zabbix_sender(key, str(num))

    def __info(self, msg, level=1):
        """
        Print messages to console
        :param msg:
        :param level:
        :return:
        """
        if self.__args.verbosity >= level:
            print msg


parser = argparse.ArgumentParser(description='Watch and recover processes')
parser.add_argument("-v", "--verbose", dest="verbosity",
                    action="count", default=0,
                    help="increases log verbosity for each occurrence.")
parser.add_argument("-pd", "--print-discovery", dest="print_discovery",
                    action='store_true',
                    help="Dump the discovery json object to the console. Forces sending of the discovery too.")
parser.set_defaults(print_discovery=False)
parser.add_argument("-pj", "--print-jobs", dest="print_jobs",
                    action='store_true',
                    help="Dump the list of watch jobs")
parser.set_defaults(print_jobs=False)
parser.add_argument("-pg", "--print-groups", dest="print_groups",
                    action='store_true',
                    help="Dump the list of groups")
parser.set_defaults(print_groups=False)
parser.add_argument("-c", "--config", dest="config",
                    help="Location of config. If not given ~/.watch-and-recover.cfg is taken.")
parser.set_defaults(config='~/.watch-and-recover.cfg')
args = parser.parse_args(sys.argv[1:])

WatchAndRecover(args)
