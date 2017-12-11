import sys
import os
import uuid
from packaging import version
from fog05.interfaces.States import State
from fog05.interfaces.RuntimePlugin import *
from LXDEntity import LXDEntity
from jinja2 import Environment
import json
import random
import time
import re
from pylxd import Client

class LXD(RuntimePlugin):

    def __init__(self, name, version, agent, plugin_uuid):
        super(LXD, self).__init__(version, plugin_uuid)
        self.name = name
        self.agent = agent
        self.agent.logger.info('__init__()', ' Hello from LXD Plugin')
        self.BASE_DIR = "/opt/fos/lxd"
        self.DISK_DIR = "disks"
        self.IMAGE_DIR = "images"
        self.LOG_DIR = "logs"
        self.HOME = str("runtime/%s/entity" % self.uuid)
        file_dir = os.path.dirname(__file__)
        self.DIR = os.path.abspath(file_dir)
        self.conn = None
        self.startRuntime()

    def startRuntime(self):
        self.agent.logger.info('startRuntime()', ' LXD Plugin - Connecting to LXD')
        self.conn = Client()
        self.agent.logger.info('startRuntime()', '[ DONE ] LXD Plugin - Connecting to LXD')
        uri = str('%s/%s/*' % (self.agent.dhome, self.HOME))
        self.agent.logger.info('startRuntime()', ' LXD Plugin - Observing %s' % uri)
        self.agent.dstore.observe(uri, self.__react_to_cache)

        '''check if dirs exists if not exists create'''
        if self.agent.getOSPlugin().dirExists(self.BASE_DIR):
            if not self.agent.getOSPlugin().dirExists(str("%s/%s") % (self.BASE_DIR, self.DISK_DIR)):
                self.agent.getOSPlugin().createDir(str("%s/%s") % (self.BASE_DIR, self.DISK_DIR))
            if not self.agent.getOSPlugin().dirExists(str("%s/%s") % (self.BASE_DIR, self.IMAGE_DIR)):
                self.agent.getOSPlugin().createDir(str("%s/%s") % (self.BASE_DIR, self.IMAGE_DIR))
            if not self.agent.getOSPlugin().dirExists(str("%s/%s") % (self.BASE_DIR, self.LOG_DIR)):
                self.agent.getOSPlugin().createDir(str("%s/%s") % (self.BASE_DIR, self.LOG_DIR))
        else:
            self.agent.getOSPlugin().createDir(str("%s") % self.BASE_DIR)
            self.agent.getOSPlugin().createDir(str("%s/%s") % (self.BASE_DIR, self.DISK_DIR))
            self.agent.getOSPlugin().createDir(str("%s/%s") % (self.BASE_DIR, self.IMAGE_DIR))
            self.agent.getOSPlugin().createDir(str("%s/%s") % (self.BASE_DIR, self.LOG_DIR))


        return self.uuid

    def stopRuntime(self):
        self.agent.logger.info('stopRuntime()', ' LXD Plugin - Destroying running domains')
        for k in list(self.current_entities.keys()):
            entity = self.current_entities.get(k)
            if entity.getState() == State.PAUSED:
                self.resumeEntity(k)
                self.stopEntity(k)
                self.cleanEntity(k)
                self.undefineEntity(k)
            if entity.getState() == State.RUNNING:
                self.stopEntity(k)
                self.cleanEntity(k)
                self.undefineEntity(k)
            if entity.getState() == State.CONFIGURED:
                self.cleanEntity(k)
                self.undefineEntity(k)
            if entity.getState() == State.DEFINED:
                self.undefineEntity(k)

        self.conn = None
        self.agent.logger.info('stopRuntime()', '[ DONE ] LXD Plugin - Bye Bye')

    def getEntities(self):
        return self.current_entities

    def defineEntity(self, *args, **kwargs):
        """
        Try defining vm
        generating xml from templates/vm.xml with jinja2
        """
        self.agent.logger.info('defineEntity()', ' LXD Plugin - Defining a Container')
        if len(args) > 0:
            entity_uuid = args[4]
            '''
                The plugin should never enter here!!!
            '''
        elif len(kwargs) > 0:
            entity_uuid = kwargs.get('entity_uuid')
            entity = LXDEntity(entity_uuid, kwargs.get('name'), kwargs.get('networks'), kwargs.get('base_image'),
                               kwargs.get('user-data'), kwargs.get('ssh-key'), kwargs.get('storage'),
                               kwargs.get("profiles"))
        else:
            return None

        entity.setState(State.DEFINED)
        self.current_entities.update({entity_uuid: entity})
        uri = str('%s/%s/%s' % (self.agent.dhome, self.HOME, entity_uuid))
        vm_info = json.loads(self.agent.dstore.get(uri))
        vm_info.update({"status": "defined"})
        self.__update_actual_store(entity_uuid, vm_info)
        self.agent.logger.info('defineEntity()', '[ DONE ] LXD Plugin - Container uuid: %s' % entity_uuid)
        return entity_uuid

    def undefineEntity(self, entity_uuid):

        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('undefineEntity()', ' LXD Plugin - Undefine a Container uuid %s ' % entity_uuid)
        entity = self.current_entities.get(entity_uuid, None)
        if entity is None:
            self.agent.logger.error('undefineEntity()', 'LXD Plugin - Entity not exists')
            raise EntityNotExistingException("Enitity not existing",
                                             str("Entity %s not in runtime %s" % (entity_uuid, self.uuid)))
        elif entity.getState() != State.DEFINED:
            self.agent.logger.error('undefineEntity()', 'LXD Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException("Entity is not in DEFINED state",
                                                     str("Entity %s is not in DEFINED state" % entity_uuid))
        else:
            self.current_entities.pop(entity_uuid, None)
            self.__pop_actual_store(entity_uuid)
            self.agent.logger.info('undefineEntity()', '[ DONE ] LXD Plugin - Undefine a Container uuid %s ' %
                                   entity_uuid)
            return True

    def configureEntity(self, entity_uuid):

        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('configureEntity()', ' LXD Plugin - Configure a Container uuid %s ' % entity_uuid)
        entity = self.current_entities.get(entity_uuid, None)
        if entity is None:
            self.agent.logger.error('configureEntity()', 'LXD Plugin - Entity not exists')
            raise EntityNotExistingException("Enitity not existing",
                                             str("Entity %s not in runtime %s" % (entity_uuid, self.uuid)))
        elif entity.getState() != State.DEFINED:
            self.agent.logger.error('configureEntity()', 'LXD Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException("Entity is not in DEFINED state",
                                                     str("Entity %s is not in DEFINED state" % entity_uuid))
        else:

            ''' 
                See if is possible to:
                - Put rootfs and images inside a custom path
            '''
            image_name = entity.image.split('/')[-1]
            wget_cmd = str('wget %s -O %s/%s/%s' % (entity.image, self.BASE_DIR, self.IMAGE_DIR, image_name))

            self.agent.getOSPlugin().executeCommand(wget_cmd, True)

            image_data = self.agent.getOSPlugin().readBinaryFile("%s/%s/%s" % (self.BASE_DIR, self.IMAGE_DIR, image_name))

            img = self.conn.images.create(image_data, public=True, wait=True)
            img.add_alias(entity_uuid, description=entity.name)


            '''
                Should explore how to setup correctly the networking, seems that you can't decide the interface you 
                want to attach to the container
                Below there is a try using a profile customized for network
            '''
            custom_profile_for_network = self.conn.profiles.create(entity_uuid)

            #WAN=$(awk '$2 == 00000000 { print $1 }' /proc/net/route)
            ## eno1

            dev = self.__generate_custom_profile_devices_configuration(entity)
            custom_profile_for_network.devices = dev
            custom_profile_for_network.save()
            if entity.profiles is None:
                entity.profiles = list()

            entity.profiles.append(entity_uuid)

            config = self.__generate_container_dict(entity)

            self.conn.containers.create(config, wait=True)

            entity.onConfigured(config)
            self.current_entities.update({entity_uuid: entity})

            uri = str('%s/%s/%s' % (self.agent.dhome, self.HOME, entity_uuid))
            container_info = json.loads(self.agent.dstore.get(uri))
            container_info.update({"status": "configured"})
            self.__update_actual_store(entity_uuid, container_info)
            self.agent.logger.info('configureEntity()', '[ DONE ] LXD Plugin - Configure a Container uuid %s ' %
                                   entity_uuid)
            return True

    def cleanEntity(self, entity_uuid):

        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('cleanEntity()', ' LXD Plugin - Clean a Container uuid %s ' % entity_uuid)
        entity = self.current_entities.get(entity_uuid, None)
        if entity is None:
            self.agent.logger.error('cleanEntity()', 'LXD Plugin - Entity not exists')
            raise EntityNotExistingException("Enitity not existing",
                                             str("Entity %s not in runtime %s" % (entity_uuid, self.uuid)))
        elif entity.getState() != State.CONFIGURED:
            self.agent.logger.error('cleanEntity()', 'LXD Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException("Entity is not in CONFIGURED state",
                                                     str("Entity %s is not in CONFIGURED state" % entity_uuid))
        else:

            c = self.conn.containers.get(entity.name)
            c.delete()

            img = self.conn.images.get_by_alias(entity_uuid)
            img.delete()

            time.sleep(2)
            profile = self.conn.profiles.get(entity_uuid)
            profile.delete()

            '''
            {'wan': {'nictype': 'physical', 'name': 'wan', 'type': 'nic', 'parent': 'veth-af90f'}, 
            'root': {'type': 'disk', 'pool': 'default', 'path': '/'}, 
            'mgmt': {'nictype': 'bridged', 'name': 'mgmt', 'type': 'nic', 'parent': 'br-45873fb0'}}

            '''


            self.agent.getOSPlugin().removeFile(str("%s/%s/%s") % (self.BASE_DIR, self.IMAGE_DIR, entity.image))

            entity.onClean()
            self.current_entities.update({entity_uuid: entity})

            uri = str('%s/%s/%s' % (self.agent.dhome, self.HOME, entity_uuid))
            container_info = json.loads(self.agent.dstore.get(uri))
            container_info.update({"status": "cleaned"})
            self.__update_actual_store(entity_uuid, container_info)
            self.agent.logger.info('cleanEntity()', '[ DONE ] LXD Plugin - Clean a Container uuid %s ' % entity_uuid)

            return True

    def runEntity(self, entity_uuid):
        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('runEntity()', ' LXD Plugin - Starting a Container uuid %s ' % entity_uuid)
        entity = self.current_entities.get(entity_uuid,None)
        if entity is None:
            self.agent.logger.error('runEntity()', 'LXD Plugin - Entity not exists')
            raise EntityNotExistingException("Enitity not existing",
                                             str("Entity %s not in runtime %s" % (entity_uuid, self.uuid)))
        elif entity.getState() == State.RUNNING:
            self.agent.logger.warning('runEntity()', 'LXD Plugin - Entity already running, some strange dput/put happen! eg after migration"')
        elif entity.getState() != State.CONFIGURED:
            self.agent.logger.error('runEntity()', 'LXD Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException("Entity is not in CONFIGURED state",
                                                     str("Entity %s is not in CONFIGURED state" % entity_uuid))
        else:

            c = self.conn.containers.get(entity.name)
            c.start()

            entity.onStart()
            uri = str('%s/%s/%s' % (self.agent.dhome, self.HOME, entity_uuid))
            container_info = json.loads(self.agent.dstore.get(uri))
            container_info.update({"status": "run"})
            self.__update_actual_store(entity_uuid, container_info)

            self.current_entities.update({entity_uuid: entity})
            self.agent.logger.info('runEntity()', '[ DONE ] LXD Plugin - Starting a Container uuid %s ' % entity_uuid)
            return True

    def stopEntity(self, entity_uuid):
        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('stopEntity()', ' LXD Plugin - Stop a Container uuid %s ' % entity_uuid)
        entity = self.current_entities.get(entity_uuid, None)
        if entity is None:
            self.agent.logger.error('stopEntity()', 'LXD Plugin - Entity not exists')
            raise EntityNotExistingException("Enitity not existing",
                                             str("Entity %s not in runtime %s" % (entity_uuid, self.uuid)))
        elif entity.getState() != State.RUNNING:
            self.agent.logger.error('stopEntity()', 'LXD Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException("Entity is not in RUNNING state",
                                                     str("Entity %s is not in RUNNING state" % entity_uuid))
        else:

            c = self.conn.containers.get(entity.name)
            c.stop()
            c.sync()

            while c.status != 'Stopped':
                c.sync()
                pass


            entity.onStop()
            self.current_entities.update({entity_uuid: entity})

            uri = str('%s/%s/%s' % (self.agent.dhome, self.HOME, entity_uuid))
            container_info = json.loads(self.agent.dstore.get(uri))
            container_info.update({"status": "stop"})
            self.__update_actual_store(entity_uuid, container_info)
            self.agent.logger.info('stopEntity()', '[ DONE ] LXD Plugin - Stop a Container uuid %s ' % entity_uuid)

            return True

    def pauseEntity(self, entity_uuid):
        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('pauseEntity()', ' LXD Plugin - Pause a Container uuid %s ' % entity_uuid)
        entity = self.current_entities.get(entity_uuid, None)
        if entity is None:
            self.agent.logger.error('pauseEntity()', 'LXD Plugin - Entity not exists')
            raise EntityNotExistingException("Enitity not existing",
                                             str("Entity %s not in runtime %s" % (entity_uuid, self.uuid)))
        elif entity.getState() != State.RUNNING:
            self.agent.logger.error('pauseEntity()', 'LXD Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException("Entity is not in RUNNING state",
                                                     str("Entity %s is not in RUNNING state" % entity_uuid))
        else:
            c = self.conn.containers.get(entity.name)
            c.freeze()

            entity.onPause()
            self.current_entities.update({entity_uuid: entity})
            uri = str('%s/%s/%s' % (self.agent.dhome, self.HOME, entity_uuid))
            container_info = json.loads(self.agent.dstore.get(uri))
            container_info.update({"status": "pause"})
            self.__update_actual_store(entity_uuid, container_info)
            self.agent.logger.info('pauseEntity()', '[ DONE ] LXD Plugin - Pause a Container uuid %s ' % entity_uuid)
            return True

    def resumeEntity(self, entity_uuid):
        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('resumeEntity()', ' LXD Plugin - Resume a Container uuid %s ' % entity_uuid)
        entity = self.current_entities.get(entity_uuid,None)
        if entity is None:
            self.agent.logger.error('resumeEntity()', 'LXD Plugin - Entity not exists')
            raise EntityNotExistingException("Enitity not existing",
                                             str("Entity %s not in runtime %s" % (entity_uuid, self.uuid)))
        elif entity.getState() != State.PAUSED:
            self.agent.logger.error('resumeEntity()', 'LXD Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException("Entity is not in PAUSED state",
                                                     str("Entity %s is not in PAUSED state" % entity_uuid))
        else:
            c = self.conn.containers.get(entity.name)
            c.unfreeze()

            entity.onResume()
            self.current_entities.update({entity_uuid: entity})

            uri = str('%s/%s/%s' % (self.agent.dhome, self.HOME, entity_uuid))
            vm_info = json.loads(self.agent.dstore.get(uri))
            vm_info.update({"status": "run"})
            self.__update_actual_store(entity_uuid, vm_info)
            self.agent.logger.info('resumeEntity()', '[ DONE ] LXD Plugin - Resume a Container uuid %s ' % entity_uuid)
            return True


    def migrateEntity(self, entity_uuid,  dst=False):
        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('migrateEntity()', ' LXD Plugin - Migrate a Container uuid %s ' % entity_uuid)
        entity = self.current_entities.get(entity_uuid, None)
        if entity is None:
            if dst is True:
                self.agent.logger.info('migrateEntity()', " LXD Plugin - I\'m the Destination Node")
                self.beforeMigrateEntityActions(entity_uuid, True)

                '''
                    migration steps from destination node
                '''

                self.afterMigrateEntityActions(entity_uuid, True)
                self.agent.logger.info('migrateEntity()', '[ DONE ] LXD Plugin - Migrate a Container uuid %s ' %
                                       entity_uuid)
                return True

            else:
                self.agent.logger.error('migrateEntity()', 'LXD Plugin - Entity not exists')
                raise EntityNotExistingException("Enitity not existing",
                                                 str("Entity %s not in runtime %s" % (entity_uuid, self.uuid)))
        elif entity.getState() != State.RUNNING:
            self.agent.logger.error('migrateEntity()', 'LXD Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException("Entity is not in RUNNING state",
                                                     str("Entity %s is not in RUNNING state" % entity_uuid))
        else:
            self.agent.logger.info('migrateEntity()', " LXD Plugin - I\'m the Source Node")
            self.beforeMigrateEntityActions(entity_uuid)
            self.afterMigrateEntityActions(entity_uuid)


    def beforeMigrateEntityActions(self, entity_uuid, dst=False):
        if dst is True:
            self.agent.logger.info('beforeMigrateEntityActions()', ' LXD Plugin - Before Migration Destination')

            #self.current_entities.update({entity_uuid: entity})

            #entity_info.update({"status": "landing"})
            #self.__update_actual_store(entity_uuid, cont_info)

            return True
        else:
            self.agent.logger.info('beforeMigrateEntityActions()', ' LXD Plugin - Before Migration Source: get information about destination node')


            return True

    def afterMigrateEntityActions(self, entity_uuid, dst=False):
        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        entity = self.current_entities.get(entity_uuid, None)
        if entity is None:
            self.agent.logger.error('afterMigrateEntityActions()', 'LXD Plugin - Entity not exists')
            raise EntityNotExistingException("Enitity not existing",
                                             str("Entity %s not in runtime %s" % (entity_uuid, self.uuid)))
        elif entity.getState() not in (State.TAKING_OFF, State.LANDING, State.RUNNING):
            self.agent.logger.error('afterMigrateEntityActions()', 'LXD Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException("Entity is not in correct state",
                                                     str("Entity %s is not in correct state" % entity.getState()))
        else:
            if dst is True:
                '''
                Here the plugin also update to the current status, and remove unused keys
                '''
                self.agent.logger.info('afterMigrateEntityActions()', ' LXD Plugin - After Migration Destination: Updating state')
                entity.state = State.RUNNING
                self.current_entities.update({entity_uuid: entity})

                uri = str('%s/%s/%s' % (self.agent.dhome, self.HOME, entity_uuid))
                cont_info = json.loads(self.agent.dstore.get(uri))
                cont_info.update({"status": "run"})
                self.__update_actual_store(entity_uuid, cont_info)

                return True
            else:
                '''
                Source node destroys all information about vm
                '''
                self.agent.logger.info('afterMigrateEntityActions()', ' LXD Plugin - After Migration Source: Updating state, destroy container')
                entity.state = State.CONFIGURED
                self.current_entities.update({entity_uuid: entity})
                self.cleanEntity(entity_uuid)
                self.undefineEntity(entity_uuid)
                return True

    def __react_to_cache(self, uri, value, v):
        self.agent.logger.info('__react_to_cache()', ' LXD Plugin - React to to URI: %s Value: %s Version: %s' % (uri, value, v))
        if value is None and v is None:
            self.agent.logger.info('__react_to_cache()', ' LXD Plugin - This is a remove for URI: %s' % uri)
        else:
            uuid = uri.split('/')[-1]
            value = json.loads(value)
            action = value.get('status')
            entity_data = value.get('entity_data')
            react_func = self.__react(action)
            if react_func is not None and entity_data is None:
                react_func(uuid)
            elif react_func is not None:
                entity_data.update({'entity_uuid': uuid})
                if action == 'define':
                    react_func(**entity_data)
                else:
                    if action == 'landing':
                        react_func(entity_data, dst=True)
                    else:
                        react_func(entity_data)

    def __random_mac_generator(self):
        mac = [0x00, 0x16, 0x3e,
               random.randint(0x00, 0x7f),
               random.randint(0x00, 0xff),
               random.randint(0x00, 0xff)]
        return ':'.join(map(lambda x: "%02x" % x, mac))

    def __lookup_by_uuid(self, uuid):
        domains = self.conn.listAllDomains(0)
        if len(domains) != 0:
            for domain in domains:
                if str(uuid) == domain.UUIDString():
                    return domain
        else:
            return None

    def __wait_boot(self, filename, configured=False):
        time.sleep(5)
        if configured:
            boot_regex = r"\[.+?\].+\[.+?\]:.+Cloud-init.+?v..+running.+'modules:final'.+Up.([0-9]*\.?[0-9]+).+seconds.\n"
        else:
            boot_regex = r".+?login:()"
        while True:
            file = open(filename, 'r')
            import os
            # Find the size of the file and move to the end
            st_results = os.stat(filename)
            st_size = st_results[6]
            file.seek(st_size)

            while 1:
                where = file.tell()
                line = file.readline()
                if not line:
                    time.sleep(1)
                    file.seek(where)
                else:
                    m = re.search(boot_regex, str(line))
                    if m:
                        found = m.group(1)
                        return found

    def __generate_custom_profile_devices_configuration(self, entity):
        '''
        template = '[ {% for net in networks %}' \
                '{"eth{{loop.index -1 }}": ' \
                '{"name": "eth{{loop.index -1}}",' \
                '"type" : "nic",'  \
                '"parent": "{{ net.intf_name }}",' \
                '"nictype": "bridged" ,' \
                '"hwaddr" : {{ net.mac }} }"
                '{% endfor %} ]'
        '''
        devices = {}
        template_value_bridge = '{"name":"%s","type":"nic","parent":"%s","nictype":"bridged"}'
        template_value_phy = '{"name":"%s","type":"nic","parent":"%s","nictype":"physical"}'
        template_value_macvlan = '{"name":"%s","type":"nic","parent":"%s","nictype":"macvlan"}'

        '''
        # Create tenant's storage pool
        # TODO: allow more storage backends
        lxc storage create $TENANT dir

        # Add a root disk to the tenant's profile
        lxc profile device add $TENANT root disk path=/ pool=$TENANT
        
        '''

        template_disk = '{"path":"%s","type":"disk","pool":"%s"}'

        template_key = '%s'
        template_key2 = "eth%d"
        for i, n in enumerate(entity.networks):
            if n.get('network_uuid') is not None:
                nws = self.agent.getNetworkPlugin(None).get(list(self.agent.getNetworkPlugin(None).keys())[0])
                # print(nws.getNetworkInfo(n.get('network_uuid')))
                br_name = nws.getNetworkInfo(n.get('network_uuid')).get('virtual_device')
                # print(br_name)
                n.update({'br_name': br_name})
                if n.get('intf_name') is None:
                    n.update({'intf_name': "eth"+str(i)})
                #nw_k = str(template_key % n.get('intf_name'))
                nw_k = str(template_key2 % i)
                nw_v = json.loads(str(template_value_bridge % (n.get('intf_name'), n.get('br_name'))))

            elif self.agent.getOSPlugin().get_intf_type(n.get('br_name')) in ['ethernet']:
                #if n.get('')
                #cmd = "sudo ip link add name %s link %s type macvlan"
                #veth_name = str('veth-%s' % entity.uuid[:5])
                #cmd = str(cmd % (veth_name, n.get('br_name')))
                #self.agent.getOSPlugin().executeCommand(cmd, True)
                #nw_v = json.loads(str(template_value_phy % (n.get('intf_name'), veth_name)))
                nw_v = json.loads(str(template_value_macvlan % (n.get('intf_name'), n.get('br_name'))))
                #nw_k = str(template_key % n.get('intf_name'))
                nw_k = str(template_key2 % i)
                #self.agent.getOSPlugin().set_interface_unaviable(n.get('br_name'))
            elif self.agent.getOSPlugin().get_intf_type(n.get('br_name')) in ['wireless']:
                nw_v = json.loads(str(template_value_phy % (n.get('intf_name'), n.get('br_name'))))
                #nw_k = str(template_key % n.get('intf_name'))
                nw_k = str(template_key2 % i)
                self.agent.getOSPlugin().set_interface_unaviable(n.get('br_name'))
            else:
                if n.get('intf_name') is None:
                    n.update({'intf_name': "eth" + str(i)})
                #nw_k = str(template_key % n.get('intf_name'))
                nw_k = str(template_key2 % i)
                nw_v = json.loads(str(template_value_bridge % (n.get('intf_name'), n.get('br_name'))))

            devices.update({nw_k: nw_v})

        lxd_version = self.conn.host_info['environment']['server_version']
        if version.parse(lxd_version) >= version.parse("2.20"):
            if entity.storage is None or len(entity.storage) == 0:
                st_n = "root"
                st_v = json.loads(str(template_disk % ("/","default")))
                devices.update({st_n: st_v})
            else:
                for s in entity.storage:
                    st_n = s.get("name")
                    st_v = json.loads(str(template_disk % (s.get("path"), s.get("pool"))))
                    devices.update({st_n: st_v})

        #devices = Environment().from_string(template)
        #devices = devices.render(networks=entity.networks)
        return devices

    def __generate_container_dict(self, entity):
        conf = {'name': entity.name, "profiles":  entity.profiles,
                'source': {'type': 'image', 'alias': entity.uuid}}
        return conf

    def __update_actual_store(self, uri, value):
        uri = str("%s/%s/%s" % (self.agent.ahome, self.HOME, uri))
        value = json.dumps(value)
        self.agent.astore.put(uri, value)

    def __pop_actual_store(self, uri,):
        uri = str("%s/%s/%s" % (self.agent.ahome, self.HOME, uri))
        self.agent.astore.remove(uri)



    def __react(self, action):
        r = {
            'define': self.defineEntity,
            'configure': self.configureEntity,
            'clean': self.cleanEntity,
            'undefine': self.undefineEntity,
            'stop': self.stopEntity,
            'resume': self.resumeEntity,
            'run': self.runEntity
            #'landing': self.migrateEntity,
            #'taking_off': self.migrateEntity
        }

        return r.get(action, None)
