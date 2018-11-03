## At a glance
watch-and-recover.py is a simple script to supervise a bunch of processes and restart them if encountered dead.

Unlike other solutions,  processes can be grouped to create dependencies between them. The recovery of a process might stop and restart other processes which are under the control of watch-and-recover too. 

For example, your app consists of several sub-processes which are all supervised. In case one of the sub-processes is not running a recovery script must be triggered which restarts the whole application shutting down all the sub-processes to restart them. 

The script is intended to be run from cron by the user who is allowed to execute the recovery action.

The script has very few dependencies so it can be used on Linux and AIX. 

## Installation
Install the script in some directory and make it executable
```bash
curl -L -s https://github.com/thorstenkramm/watch-and-recover/raw/master/watch-and-recover.py > /usr/local/bin/watch-and-recover
chmod +x /usr/local/bin/watch-and-recover
```
Create a configuration using the [example](watch-and-recover.cfg) and adjust it to your needs.

## Monitoring with Zabbix
The script can report the status of all supervised processes and the status of the recovery actions to a Zabbix-Server using a local installed Zabbix-Sender. In order to connect the script to a Zabbix-Server you must import the appropriated template and assign it to the host the script runs on. 
`zabbix_sender` is invoked using the configuration from Zabbix-Agent. Zabbix-Agent Active mode should be configured successfully before.

The usage of Zabbix-Sender is optional. If you remove the `zabbix_sender_bin` option from the config file, the script works well without sending any data to a Zabbix-Server.

## Usage
`watch-and-recover -h` prints a brief help message. 

```bash
usage: watch-and-recover.py [-h] [-v] [-pd] [-pj] [-pg] [-c CONFIG]

Watch and recover processes

optional arguments:
  -h, --help            show this help message and exit
  -v, --verbose         increases log verbosity for each occurrence.
  -pd, --print-discovery
                        Dump the discovery json object to the console. Forces
                        sending of the discovery too.
  -pj, --print-jobs     Dump the list of watch jobs
  -pg, --print-groups   Dump the list of groups
  -c CONFIG, --config CONFIG
                        Location of config. If not given ~/.watch-and-
                        recover.cfg is taken.
```

If the config is placed in `~/.watch-and-recover.cfg` the script can be invoked without any parameters.


 ## Example
Look at the following snipped for a config file
```
[watch:app]
watch_for = java -jar app.jar
recover_with = recovery.sh full
group = my_app

[watch:sub1]
watch_for = java -jar app.jar -Dproperty.foo=sub1
recover_with = recovery.sh sub1
group = my_app

[watch:sub2]
watch_for = java -jar app.jar -Dproperty.foo=sub2
recover_with = recovery.sh sub2
group = my_app

[group:my_app]
delay = 300
tries = 3
cwd = /opt/my_app/scripts/
```
The jobs are processed in order of appearance. 
On the command line `ps -ef` is executed to get the list of running processes. The `watch_for` string is taken as a regular expression and matched against the CMD-column of the ps command. 

If no match is found for `java -jar app.jar`  a `cd /opt/my_app/scripts/`  and `nohup ./recovery.sh full&` is executed.
Now the clock runs for 300 seconds. During this period all recovery actions of the same group are ignored.
If `java -jar app.jar` or any other process of the group cannot be recovered two more tries are executed. If the three tries have been reached, no more actions are taken for any process of the group.
The clock and the tries-counter are reset if all processes of the group are running again.



