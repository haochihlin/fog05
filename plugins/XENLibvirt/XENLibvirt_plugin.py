import sys
import os
import uuid
from fog05.interfaces.States import State
from fog05.interfaces.RuntimePlugin import *
from XENLibvirtEntity import XENLibvirtEntity
from XENLibvirtEntityInstance import XENLibvirtEntityInstance
from jinja2 import Environment
import json
import random
import time
import re
import libvirt
import ipaddress

class XENLibvirt(RuntimePlugin):

    def __init__(self, name, version, agent, plugin_uuid, hypervisor, user=None):
        super(XENLibvirt, self).__init__(version, plugin_uuid)
        self.name = name
        self.agent = agent
        self.hypervisor = hypervisor
        self.agent.logger.info('__init__()', ' Hello from XEN Plugin')
        self.BASE_DIR = os.path.join(self.agent.base_path, 'xen')
        self.DISK_DIR = 'disks'
        self.IMAGE_DIR = 'images'
        self.LOG_DIR = 'logs'
        self.HOME = 'runtime/{}/entity'.format(self.uuid)
        self.INSTANCE = 'instance'
        file_dir = os.path.dirname(__file__)
        self.DIR = os.path.abspath(file_dir)
        self.conn = None
        self.user = 'fog05'
        if user != None:
            self.user = user

        self.start_runtime()



    def start_runtime(self):
        self.agent.logger.info('startRuntime()', ' XEN Plugin - Connecting to XEN')
        self.__connect_to_hypervisor(self.hypervisor)
        self.agent.logger.info('startRuntime()', '[ DONE ] XEN Plugin - Connecting to XEN')
        uri = '{}/{}/*'.format(self.agent.dhome, self.HOME)
        self.agent.logger.info('startRuntime()',' XEN Plugin - Observing %s' % uri)
        self.agent.dstore.observe(uri, self.__react_to_cache)

        '''
        These directories should be created at dom0
        dom0 is for sure a linux kernel with basic linux command
        '''


        # if self.agent.get_os_plugin().dir_exists(self.BASE_DIR):
        #     if not self.agent.get_os_plugin().dir_exists(os.path.join(self.BASE_DIR, self.DISK_DIR)):
        #         self.agent.get_os_plugin().create_dir(os.path.join(self.BASE_DIR, self.DISK_DIR))
        #     if not self.agent.get_os_plugin().dir_exists(os.path.join(self.BASE_DIR, self.IMAGE_DIR)):
        #         self.agent.get_os_plugin().create_dir(os.path.join(self.BASE_DIR, self.IMAGE_DIR))
        #     if not self.agent.get_os_plugin().dir_exists(os.path.join(self.BASE_DIR, self.LOG_DIR)):
        #         self.agent.get_os_plugin().create_dir(os.path.join(self.BASE_DIR, self.LOG_DIR))
        # else:


        self.__execture_on_dom0(self.hypervisor,'mkdir {}'.format(self.BASE_DIR))
        self.__execture_on_dom0(self.hypervisor, 'mkdir {}'.format(os.path.join(self.BASE_DIR, self.DISK_DIR)))
        self.__execture_on_dom0(self.hypervisor, 'mkdir {}'.format(os.path.join(self.BASE_DIR, self.IMAGE_DIR)))
        self.__execture_on_dom0(self.hypervisor, 'mkdir {}'.format(os.path.join(self.BASE_DIR, self.LOG_DIR)))



        return self.uuid

    def stop_runtime(self):
        self.agent.logger.info('stopRuntime()', ' XEN Plugin - Destroying running domains')
        for k in list(self.current_entities.keys()):
            entity = self.current_entities.get(k)
            for i in list(entity.instances.keys()):
                self.__force_entity_instance_termination(k, i)
            if entity.get_state() == State.DEFINED:
                self.undefine_entity(k)

        self.conn.close()
        self.agent.logger.info('stopRuntime()', '[ DONE ] XEN Plugin - Bye Bye')

    def get_entities(self):
        return self.current_entities

    def define_entity(self, *args, **kwargs):
        '''

        This means that this plugin should interact with the dom0 to make it download and create the images

        '''
        self.agent.logger.info('define_entity()', ' XEN Plugin - Defining a VM')

        if len(args) > 0:
            entity_uuid = args[4]
            disk_path = '{}.qcow2'.format(entity_uuid)
            cdrom_path = '{}_config.iso'.format(entity_uuid)
            disk_path = os.path.join(self.BASE_DIR, self.DISK_DIR, disk_path)
            cdrom_path = os.path.join(self.BASE_DIR, self.DISK_DIR, cdrom_path)
            entity = XENLibvirtEntity(entity_uuid, args[0], args[2], args[1], disk_path, args[3], cdrom_path, [],
                                   args[5], args[6], args[7])
        elif len(kwargs) > 0:
            entity_uuid = kwargs.get('entity_uuid')
            disk_path = '{}.qcow2'.format(entity_uuid)
            cdrom_path = '{}_config.iso'.format(entity_uuid)
            disk_path = os.path.join(self.BASE_DIR, self.DISK_DIR, disk_path)
            cdrom_path = os.path.join(self.BASE_DIR, self.DISK_DIR, cdrom_path)
            entity = XENLibvirtEntity(entity_uuid, kwargs.get('name'), kwargs.get('cpu'), kwargs.get('memory'), disk_path,
                                      kwargs.get('disk_size'), cdrom_path, kwargs.get('networks'),
                                      kwargs.get('base_image'), kwargs.get('user-data'), kwargs.get('ssh-key'))
        else:
            return None

        image_name = os.path.join(self.BASE_DIR, self.IMAGE_DIR, entity.image_url.split('/')[-1])

        self.__execture_on_dom0(self.hypervisor, 'wget {} -O {}'.format(entity.image_url, image_name))
        #self.agent.get_os_plugin().download_file(entity.image_url, image_name)
        entity.image = image_name

        entity.set_state(State.DEFINED)
        self.current_entities.update({entity_uuid: entity})
        uri = '{}/{}/{}'.format(self.agent.dhome, self.HOME, entity_uuid)
        vm_info = json.loads(self.agent.dstore.get(uri))
        vm_info.update({'status': 'defined'})
        self.__update_actual_store(entity_uuid, vm_info)
        self.agent.logger.info('define_entity()', '[ DONE ] XEN Plugin - VM Defined uuid: {}'.format(entity_uuid))
        return entity_uuid

    def undefine_entity(self, entity_uuid):

        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('undefine_entity()', ' XEN Plugin - Undefine a VM uuid {} '.format(entity_uuid))
        entity = self.current_entities.get(entity_uuid, None)
        if entity is None:
            self.agent.logger.error('undefine_entity()', 'XEN Plugin - Entity not exists')
            raise EntityNotExistingException('Enitity not existing', 'Entity {} not in runtime {}'.format(entity_uuid, self.uuid))
        elif entity.get_state() != State.DEFINED:
            self.agent.logger.error('undefine_entity()', 'XEN Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException('Entity is not in DEFINED state', 'Entity {} is not in DEFINED state'.format(entity_uuid))
        else:
            if(self.current_entities.pop(entity_uuid, None)) is None:
                self.agent.logger.warning('undefine_entity()', 'XEN Plugin - pop from entities dict returned none')

            for i in list(entity.instances.keys()):
                self.__force_entity_instance_termination(entity_uuid, i)

            self.__execture_on_dom0(self.hypervisor, 'rm {}'.format(os.path.join(self.BASE_DIR, self.IMAGE_DIR, entity.image)))
            #self.agent.get_os_plugin().remove_file(os.path.join(self.BASE_DIR, self.IMAGE_DIR, entity.image))
            self.__pop_actual_store(entity_uuid)
            self.agent.logger.info('undefine_entity()', '[ DONE ] XEN Plugin - Undefine a VM uuid {}'.format(entity_uuid))
            return True

    def configure_entity(self, entity_uuid, instance_uuid=None):
        '''
        :param entity_uuid:
        :param instance_uuid:
        :return:
        '''

        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('configure_entity()', ' XEN Plugin - Configure a VM uuid {}'.format(entity_uuid))
        entity = self.current_entities.get(entity_uuid, None)
        if entity is None:
            self.agent.logger.error('configure_entity()', 'XEN Plugin - Entity not exists')
            raise EntityNotExistingException('Enitity not existing','Entity {} not in runtime {}'.format(entity_uuid, self.uuid))
        elif entity.get_state() != State.DEFINED:
            self.agent.logger.error('configure_entity()','XEN Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException('Entity is not in DEFINED state','Entity {} is not in DEFINED state'.format(entity_uuid))
        else:

            if instance_uuid is None:
                instance_uuid = str(uuid.uuid4())

            if entity.has_instance(instance_uuid):
                print('This instance already existis!!')
            else:

                id = len(entity.instances)
                name = '{0}{1}'.format(entity.name, id)
                disk_path = '{}.qcow2'.format(instance_uuid)
                cdrom_path = '{}_config.iso'.format(instance_uuid)
                disk_path = os.path.join(self.BASE_DIR, self.DISK_DIR, disk_path)
                cdrom_path = os.path.join(self.BASE_DIR, self.DISK_DIR, cdrom_path)
                #uuid, name, cpu, ram, disk, disk_size, cdrom, networks, image, user_file, ssh_key, entity_uuid)
                instance = XENLibvirtEntityInstance(instance_uuid, name, entity.cpu, entity.ram, disk_path,
                                      entity.disk_size, cdrom_path, entity.networks, entity.image, entity.user_file,
                                      entity.ssh_key, entity_uuid)

                for i, n in enumerate(instance.networks):
                    # if n.get('type') in ['wifi']:
                    #
                    #     nw_ifaces =  self.agent.get_os_plugin().get_network_informations()
                    #     for iface in nw_ifaces:
                    #         if self.agent.get_os_plugin().get_intf_type(iface.get('intf_name')) == 'wireless' and iface.get('available') is True:
                    #             self.agent.get_os_plugin().set_interface_unaviable(iface.get('intf_name'))
                    #             n.update({'direct_intf': iface.get('intf_name')})
                    #     # TODO get available interface from os plugin
                    if n.get('network_uuid') is not None:
                        # TODO should get the network plugin for the XEN hypervisor
                        nws = self.agent.get_network_plugin(None).get(list(self.agent.get_network_plugin(None).keys())[0])
                        #print(nws.getNetworkInfo(n.get('network_uuid')))
                        br_name = nws.get_network_info(n.get('network_uuid')).get('virtual_device')
                        #print(br_name)
                        n.update({'br_name': br_name})
                    if n.get('intf_name') is None:
                        n.update({'intf_name': 'veth{0}'.format(i)})

                vm_xml = self.__generate_dom_xml(instance)
                #image_name = instance.image.split('/')[-1]

                #wget_cmd = 'wget %s -O %s/%s/%s' % (entity.image, self.BASE_DIR, self.IMAGE_DIR, image_name))
                #image_url = instance.image

                conf_cmd = '{} --hostname %s --uuid {}'.format(os.path.join(self.DIR, 'templates', 'create_config_drive.sh'), entity.name, instance_uuid)
                rm_temp_cmd = 'rm'

                if instance.user_file is not None and instance.user_file != '':
                    data_filename = 'userdata_{}'.format(instance_uuid)
                    self.agent.get_os_plugin().store_file(entity.user_file, self.BASE_DIR, data_filename)
                    data_filename = os.path.join(self.BASE_DIR, data_filename)
                    conf_cmd = conf_cmd + ' --user-data {}'.format(data_filename)
                    #rm_temp_cmd = rm_temp_cmd + ' %s' % data_filename)
                if instance.ssh_key is not None and instance.ssh_key != '':
                    key_filename = 'key_{}.pub'.format(instance_uuid)
                    self.agent.get_os_plugin().store_file(instance.ssh_key, self.BASE_DIR, key_filename)
                    key_filename = os.path.join(self.BASE_DIR, key_filename)
                    conf_cmd = conf_cmd + ' --ssh-key {}'.format(key_filename)
                    #rm_temp_cmd = rm_temp_cmd + ' %s' % key_filename)

                conf_cmd = conf_cmd + ' {}'.format(instance.cdrom)

                qemu_cmd = 'qemu-img create -f qcow2 {} {}G'.format(instance.disk, instance.disk_size)

                dd_cmd = 'dd if={} of={} bs=4M'.format(instance.image, instance.disk)

                #instance.image = image_name

                #self.agent.getOSPlugin().executeCommand(wget_cmd, True)
                #self.agent.getOSPlugin().downloadFile(image_url, os.path.join(self.BASE_DIR, self.IMAGE_DIR, image_name))
                self.__execture_on_dom0(self.hypervisor, qemu_cmd)
                self.__execture_on_dom0(self.hypervisor, conf_cmd)
                self.__execture_on_dom0(self.hypervisor, dd_cmd)

                if instance.ssh_key is not None and instance.ssh_key != '':
                    self.agent.get_os_plugin().remove_file(key_filename)
                if instance.user_file is not None and instance.user_file != '':
                    self.agent.get_os_plugin().remove_file(data_filename)

                    #self.agent.getOSPlugin().executeCommand(rm_temp_cmd)

                try:
                    self.conn.defineXML(vm_xml)
                except libvirt.libvirtError as err:
                    self.__connect_to_hypervisor(self.hypervisor)
                    self.conn.defineXML(vm_xml)

                instance.on_configured(vm_xml)
                entity.add_instance(instance)
                self.current_entities.update({entity_uuid: entity})

                uri = '{}/{}/{}'.format(self.agent.ahome, self.HOME, entity_uuid)
                vm_info = json.loads(self.agent.astore.get(uri))
                vm_info.update({'status': 'configured'})
                vm_info.update({'name': instance.name})

                self.__update_actual_store_instance(entity_uuid,instance_uuid, vm_info)
                #self.__update_actual_store(entity_uuid, vm_info)

                self.agent.logger.info('configure_entity()', '[ DONE ] XEN Plugin - Configure a VM uuid {}'.format(instance_uuid))
                return True

    def clean_entity(self, entity_uuid, instance_uuid=None):

        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('clean_entity()', ' XEN Plugin - Clean a VM uuid {}'.format(entity_uuid))
        entity = self.current_entities.get(entity_uuid, None)
        if entity is None:
            self.agent.logger.error('clean_entity()', 'XEN Plugin - Entity not exists')
            raise EntityNotExistingException('Enitity not existing', 'Entity {} not in runtime {}'.format(entity_uuid, self.uuid))
        elif entity.get_state() != State.DEFINED:
            self.agent.logger.error('clean_entity()', 'XEN Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException('Entity is not in DEFINED state', 'Entity {} is not in DEFINED state'.format(entity_uuid))
        else:

            if instance_uuid is None or not entity.has_instance(instance_uuid):
                self.agent.logger.error('clean_entity()','XEN Plugin - Instance not found!!')
            else:
                instance = entity.get_instance(instance_uuid)
                if instance.get_state() != State.CONFIGURED:
                    self.agent.logger.error('clean_entity()', 'XEN Plugin - Instance state is wrong, or transition not allowed')
                    raise StateTransitionNotAllowedException('Instance is not in CONFIGURED state', 'Instance {} is not in CONFIGURED state'.format(instance_uuid))
                else:
                    dom = self.__lookup_by_uuid(instance_uuid)
                    if dom is not None:
                        dom.undefine()
                    else:
                        self.agent.logger.error('clean_entity()', 'XEN Plugin - Domain not found!!')
                    rm_cmd = 'rm -f {} {} {}'.format(instance.cdrom, instance.disk, os.path.join(self.BASE_DIR, self.LOG_DIR, instance_uuid))
                    self.__execture_on_dom0(self.hypervisor, rm_cmd)

                    entity.remove_instance(instance)
                    self.current_entities.update({entity_uuid: entity})

                    self.__pop_actual_store_instance(entity_uuid, instance_uuid)
                    self.agent.logger.info('clean_entity()', '[ DONE ] XEN Plugin - Clean a VM uuid {}'.format(entity_uuid))

                return True

    def run_entity(self, entity_uuid, instance_uuid=None):
        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('run_entity()', ' XEN Plugin - Starting a VM uuid {}'.format(entity_uuid))
        entity = self.current_entities.get(entity_uuid,None)
        if entity is None:
            self.agent.logger.error('run_entity()', 'XEN Plugin - Entity not exists')
            raise EntityNotExistingException('Enitity not existing', 'Entity {} not in runtime {}'.format(entity_uuid, self.uuid))
        elif entity.get_state() != State.DEFINED:
            self.agent.logger.error('run_entity()', 'XEN Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException('Entity is not in DEFINED state', 'Entity {} is not in DEFINED state'.format(entity_uuid))
        else:
            if instance_uuid is None or not entity.has_instance(instance_uuid):
                self.agent.logger.error('run_entity()','XEN Plugin - Instance not found!!')
            else:
                instance = entity.get_instance(instance_uuid)
                if instance.get_state() != State.CONFIGURED:
                    self.agent.logger.error('clean_entity()', 'XEN Plugin - Instance state is wrong, or transition not allowed')
                    raise StateTransitionNotAllowedException('Instance is not in CONFIGURED state', 'Instance {} is not in CONFIGURED state'.format(instance_uuid))
                else:
                    self.__lookup_by_uuid(instance_uuid).create()
                    instance.on_start()
                    '''
                    Then after boot should update the `actual store` with the run status of the vm  
                    '''

                    # log_filename = '%s/%s/%s_log.log' % (self.BASE_DIR, self.LOG_DIR, instance_uuid))
                    # if instance.user_file is not None and instance.user_file != '':
                    #     self.__wait_boot(log_filename, True)
                    # else:
                    #     self.__wait_boot(log_filename)
                    # TODO check why wait boot not work

                    self.agent.logger.info('run_entity()', ' XEN Plugin - VM %s Started!' % instance)
                    uri = '{}/{}/{}/{}/{}'.format(self.agent.ahome, self.HOME, entity_uuid,self.INSTANCE, instance_uuid)
                    vm_info = json.loads(self.agent.astore.get(uri))
                    vm_info.update({'status': 'run'})
                    self.__update_actual_store_instance(entity_uuid,instance_uuid, vm_info)
                    self.current_entities.update({entity_uuid: entity})
                    self.agent.logger.info('run_entity()', '[ DONE ] XEN Plugin - Starting a VM uuid %s ' % entity_uuid)
                    return True

    def stop_entity(self, entity_uuid, instance_uuid=None):
        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('stop_entity()', ' XEN Plugin - Stop a VM uuid {}'.format(entity_uuid))
        entity = self.current_entities.get(entity_uuid, None)
        if entity is None:
            self.agent.logger.error('stop_entity()', 'XEN Plugin - Entity not exists')
            raise EntityNotExistingException('Enitity not existing', 'Entity {} not in runtime {}'.format(entity_uuid, self.uuid))
        elif entity.get_state() != State.DEFINED:
            self.agent.logger.error('stop_entity()', 'XEN Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException('Entity is not in DEFINED state', 'Entity {} is not in DEFINED state'.format(entity_uuid))
        else:
            if instance_uuid is None or not entity.has_instance(instance_uuid):
                self.agent.logger.error('run_entity()', 'XEN Plugin - Instance not found!!')
            else:
                instance = entity.get_instance(instance_uuid)
                if instance.get_state() != State.RUNNING:
                    self.agent.logger.error('stop_entity()', 'XEN Plugin - Instance state is wrong, or transition not allowed')
                    raise StateTransitionNotAllowedException('Instance is not in RUNNING state', 'Instance {} is not in RUNNING state'.format(instance_uuid))
                else:
                    self.__lookup_by_uuid(instance_uuid).destroy()
                    instance.on_stop()
                    self.current_entities.update({entity_uuid: entity})

                    uri = '{}/{}/{}/{}/{}'.format(self.agent.ahome, self.HOME, entity_uuid, self.INSTANCE, instance_uuid)
                    vm_info = json.loads(self.agent.astore.get(uri))
                    vm_info.update({'status': 'stop'})
                    self.__update_actual_store_instance(entity_uuid,instance_uuid, vm_info)
                    self.agent.logger.info('stop_entity()', '[ DONE ] XEN Plugin - Stop a VM uuid {}'.format(instance_uuid))

            return True

    def pause_entity(self, entity_uuid, instance_uuid=None):
        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('pause_entity()', ' XEN Plugin - Pause a VM uuid {}'.format(entity_uuid))
        entity = self.current_entities.get(entity_uuid, None)
        if entity is None:
            self.agent.logger.error('pause_entity()', 'XEN Plugin - Entity not exists')
            raise EntityNotExistingException('Enitity not existing', 'Entity %s not in runtime {}'.format(entity_uuid, self.uuid))
        elif entity.get_state() != State.DEFINED:
            self.agent.logger.error('pause_entity()', 'XEN Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException('Entity is not in DEFINED state', 'Entity %s is not in DEFINED state'.format(entity_uuid))
        else:
            if instance_uuid is None or not entity.has_instance(instance_uuid):
                self.agent.logger.error('run_entity()', 'XEN Plugin - Instance not found!!')
            else:
                instance = entity.get_instance(instance_uuid)
                if instance.get_state() != State.RUNNING:
                    self.agent.logger.error('pause_entity()', 'XEN Plugin - Instance state is wrong, or transition not allowed')
                    raise StateTransitionNotAllowedException('Instance is not in RUNNING state', 'Instance {} is not in RUNNING state'.format(instance_uuid))
                else:
                    self.__lookup_by_uuid(instance_uuid).suspend()
                    instance.on_pause()
                    self.current_entities.update({entity_uuid: entity})
                    uri = '{}/{}/{}/{}/{}'.format(self.agent.ahome, self.HOME, entity_uuid, self.INSTANCE, instance_uuid)
                    vm_info = json.loads(self.agent.astore.get(uri))
                    vm_info.update({'status': 'pause'})
                    self.__update_actual_store_instance(entity_uuid,instance_uuid, vm_info)
                    self.agent.logger.info('pause_entity()', '[ DONE ] XEN Plugin - Pause a VM uuid {}'.format(instance_uuid))
                    return True

    def resume_entity(self, entity_uuid, instance_uuid=None):
        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('resume_entity()', ' XEN Plugin - Resume a VM uuid {}'.format(entity_uuid))
        entity = self.current_entities.get(entity_uuid,None)
        if entity is None:
            self.agent.logger.error('resume_entity()', 'XEN Plugin - Entity not exists')
            raise EntityNotExistingException('Enitity not existing',  'Entity {} not in runtime {}'.format(entity_uuid, self.uuid))
        elif entity.get_state() != State.DEFINED:
            self.agent.logger.error('resume_entity()', 'XEN Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException('Entity is not in DEFINED state', 'Entity {} is not in DEFINED state'.format(entity_uuid))
        else:
            if instance_uuid is None or not entity.has_instance(instance_uuid):
                self.agent.logger.error('run_entity()', 'XEN Plugin - Instance not found!!')
            else:
                instance = entity.get_instance(instance_uuid)
                if instance.get_state() != State.PAUSED:
                    self.agent.logger.error('resume_entity()', 'XEN Plugin - Instance state is wrong, or transition not allowed')
                    raise StateTransitionNotAllowedException('Instance is not in PAUSED state', 'Instance {} is not in PAUSED state'.format(entity_uuid))
                else:
                    self.__lookup_by_uuid(instance_uuid).resume()
                    instance_uuid.on_resume()
                    self.current_entities.update({entity_uuid: entity})
                    uri = '{}/{}/{}/{}/{}'.format(self.agent.ahome, self.HOME, entity_uuid, self.INSTANCE, instance_uuid)
                    vm_info = json.loads(self.agent.dstore.get(uri))
                    vm_info.update({'status': 'run'})
                    self.__update_actual_store_instance(entity_uuid,instance_uuid, vm_info)
                    self.agent.logger.info('resume_entity()', '[ DONE ] XEN Plugin - Resume a VM uuid {}'.format(instance_uuid))
                    return True


    def migrate_entity(self, entity_uuid, dst=False, instance_uuid=None):
        pass
        """
        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('migrate_entity()', ' XEN Plugin - Migrate a VM uuid %s ' % entity_uuid)
        entity = self.current_entities.get(entity_uuid, None)
        if entity is None:
            if dst is True:

                self.agent.logger.info('migrate_entity()', ' XEN Plugin - I\'m the Destination Node')
                self.before_migrate_entity_actions(entity_uuid, True, instance_uuid)

                while True:  # wait for migration to be finished
                    dom = self.__lookup_by_uuid(instance_uuid)
                    if dom is None:
                        self.agent.logger.info('migrate_entity()', ' XEN Plugin - Domain not already in this host')
                        time.sleep(5)
                    else:
                        if dom.isActive() == 1:
                            break
                        else:
                            self.agent.logger.info('migrate_entity()', ' XEN Plugin - Domain in this host but not running')
                            time.sleep(5)


                self.after_migrate_entity_actions(entity_uuid, True, instance_uuid)
                self.agent.logger.info('migrate_entity()', '[ DONE ] XEN Plugin - Migrate a VM uuid %s ' % entity_uuid)
                return True

            else:
                self.agent.logger.error('migrate_entity()', 'XEN Plugin - Entity not exists')
                raise EntityNotExistingException('Enitity not existing',
                                                 'Entity %s not in runtime %s' % (entity_uuid, self.uuid)))
        elif entity.get_state() != State.DEFINED:
            self.agent.logger.error('migrate_entity()', 'XEN Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException('Entity is not in DEFINED state',
                                                     'Entity %s is not in DEFINED state' % entity_uuid))
        else:
            instance = entity.get_instance(instance_uuid)
            if instance.get_state() not in [State.RUNNING, State.TAKING_OFF]:
                self.agent.logger.error('clean_entity()',
                                        'XEN Plugin - Instance state is wrong, or transition not allowed')
                raise StateTransitionNotAllowedException('Instance is not in RUNNING state',

                                                             'Instance %s is not in RUNNING state' % entity_uuid))
            self.agent.logger.info('migrate_entity()', ' XEN Plugin - I\'m the Source Node')
            self.before_migrate_entity_actions(entity_uuid, instance_uuid=instance_uuid)
            self.after_migrate_entity_actions(entity_uuid,  instance_uuid=instance_uuid)
        """



    def before_migrate_entity_actions(self, entity_uuid, dst=False, instance_uuid=None):
        pass
        """
                if dst is True:
            self.agent.logger.info('before_migrate_entity_actions()', ' XEN Plugin - Before Migration Destination: '
                                                                     'Create Domain and destination files')
            uri = '%s/%s/%s/%s/%s' % (self.agent.dhome, self.HOME, entity_uuid, self.INSTANCE, instance_uuid))
            entity_info = json.loads(self.agent.dstore.get(uri))
            vm_info = entity_info.get('entity_data')

            entity = XENLibvirtEntity(instance_uuid, vm_info.get('name'), vm_info.get('cpu'),
                                      vm_info.get('memory'), '', vm_info.get('disk_size'), '',
                                      vm_info.get('networks'),
                                      vm_info.get('base_image'), vm_info.get('user-data'), vm_info.get('ssh-key'))
            entity.state = State.DEFINED
            image_name = os.path.join(self.BASE_DIR, self.IMAGE_DIR, entity.image.split('/')[-1])
            self.agent.get_os_plugin().download_file(entity.image_url, image_name)
            entity.image = image_name
            self.current_entities.update({entity_uuid: entity})
            self.__update_actual_store(entity_uuid, entity_info)


            id = len(entity.instances)
            name = '{0}{1}'.format(entity.name, id)
            disk_path = '%s.qcow2' % instance_uuid)
            cdrom_path = '%s_config.iso' % instance_uuid)
            disk_path = os.path.join(self.BASE_DIR, self.DISK_DIR, disk_path)
            cdrom_path = os.path.join(self.BASE_DIR, self.DISK_DIR, cdrom_path)
            instance = XENLibvirtEntityInstance(instance_uuid, name, vm_info.get('cpu'),
                vm_info.get('memory'),disk_path,vm_info.get('disk_size'), cdrom_path, vm_info.get('networks'),
                vm_info.get('base_image'), vm_info.get('user-data'), vm_info.get('ssh-key'),entity_uuid)

            instance.state = State.LANDING
            vm_info.update({'name': name})
            vm_xml = self.__generate_dom_xml(instance)

            instance.xml = vm_xml
            qemu_cmd = 'qemu-img create -f qcow2 %s %dG' % (instance.disk, instance.disk_size))
            self.agent.get_os_plugin().execute_command(qemu_cmd, True)
            self.agent.get_os_plugin().create_file(instance.cdrom)
            self.agent.get_os_plugin().create_file(os.path.join(self.BASE_DIR, self.LOG_DIR, '%s_log.log' % instance_uuid)))

            conf_cmd = '%s --hostname %s --uuid %s' % (os.path.join(self.DIR, 'templates',
                                                           'create_config_drive.sh'), instance.name, instance_uuid))
            rm_temp_cmd = 'rm')
            if instance.user_file is not None and instance.user_file != '':
                data_filename = 'userdata_%s' % instance_uuid)
                self.agent.get_os_plugin().store_file(instance.user_file, self.BASE_DIR, data_filename)
                data_filename = os.path.join(self.BASE_DIR, data_filename)
                conf_cmd = conf_cmd + ' --user-data %s' % data_filename)
                # rm_temp_cmd = rm_temp_cmd + ' %s' % data_filename)
            if instance.ssh_key is not None and instance.ssh_key != '':
                key_filename = 'key_%s.pub' % instance_uuid)
                self.agent.get_os_plugin().store_file(instance.ssh_key, self.BASE_DIR, key_filename)
                key_filename = os.path.join(self.BASE_DIR, key_filename)
                conf_cmd = conf_cmd + ' --ssh-key %s' % key_filename)
                # rm_temp_cmd = rm_temp_cmd + ' %s' % key_filename)

            conf_cmd = conf_cmd + ' %s' % instance.cdrom)

            self.agent.get_os_plugin().execute_command(qemu_cmd, True)
            #self.agent.getOSPlugin().createFile(entity.cdrom)

            self.agent.get_os_plugin().execute_command(conf_cmd, True)


            # try:
            #     self.conn.defineXML(vm_xml)
            # except libvirt.libvirtError as err:
            #     self.conn = libvirt.open('qemu:///system')
            #     self.conn.defineXML(vm_xml)

            entity_info.update({'entity_data': vm_info})
            entity_info.update({'status': 'landing'})

            entity.add_instance(instance)
            self.current_entities.update({entity_uuid: entity})

            self.__update_actual_store_instance(entity_uuid,instance_uuid, entity_info)

            return True
        else:
            self.agent.logger.info('before_migrate_entity_actions()', ' XEN Plugin - Before Migration Source: get '
                                                                     'information about destination node')
            entity = self.current_entities.get(entity_uuid, None)
            instance = entity.get_instance(instance_uuid)
            uri = '%s/%s/%s/%s/%s' % (self.agent.dhome, self.HOME, entity_uuid, self.INSTANCE, instance_uuid))
            instance_info = json.loads(self.agent.dstore.get(uri))
            fognode_uuid = instance_info.get('dst')

            uri = 'afos://<sys-id>/%s/plugins' % fognode_uuid)
            all_plugins = json.loads(self.agent.astore.get(uri)).get('plugins') # TODO: solve this ASAP

            runtimes = [x for x in all_plugins if x.get('type') == 'runtime']
            search = [x for x in runtimes if 'XENLibvirt' in x.get('name')]
            if len(search) == 0:
                self.agent.logger.error('before_migrate_entity_actions()', 'XEN Plugin - Before Migration Source: No '
                                                                          'XEN Plugin, Aborting!!!')
                exit()
            else:
                XEN = search[0]

            #uri = 'afos://<sys-id>/%s/runtime/%s/entity/%s' % (dst, XEN.get('uuid'), entity_uuid))
            #self.agent.dstore.put(uri, instance_info)

            flag = False
            while flag:
                self.agent.logger.info('before_migrate_entity_actions()', ' XEN Plugin - Before Migration Source: '
                                                                         'Waiting destination to be '
                                        'ready')
                time.sleep(1)
                uri = 'afos://<sys-id>/%s/runtime/%s/entity/%s/instance/%s' % (dst, XEN.get('uuid'), entity_uuid,
                                                                                   instance_uuid))
                vm_info = json.loads(self.agent.astore.get(uri)) # TODO: solve this ASAP
                if vm_info is not None and vm_info.get('status') == 'landing':
                        flag = True

            instance.state = State.TAKING_OFF
            instance_info.update({'status' : 'taking_off'})
            self.__update_actual_store_instance(entity_uuid,instance_uuid,instance_info)

            self.current_entities.update({entity_uuid: entity})
            uri = 'afos://<sys-id>/%s/' % fognode_uuid)

            dst_node_info = self.agent.astore.get(uri) # TODO: solve this ASAP
            if isinstance(dst_node_info, tuple):
                dst_node_info = dst_node_info[0]
            dst_node_info = dst_node_info.replace(''', ''')
            #print(dst_node_info)
            dst_node_info = json.loads(dst_node_info)
            ## json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
            # dst_node_info = json.loads(self.agent.astore.get(uri)[0])
            ##
            dom = self.__lookup_by_uuid(instance_uuid)
            nw = dst_node_info.get('network')

            dst_hostname = dst_node_info.get('name')



            dst_ip = [x for x in nw if x.get('default_gw') is True]
            # TODO: or x.get('inft_configuration').get('ipv6_gateway') for ip_v6
            if len(dst_ip) == 0:
                return False

            dst_ip = dst_ip[0].get('inft_configuration').get('ipv4_address') # TODO: as on search should use ipv6

            # ## ADDING TO /etc/hosts otherwise migration can fail
            self.agent.get_os_plugin().add_know_host(dst_hostname, dst_ip)
            ###

            # ## ACTUAL MIGRATIION ##################
            dst_host = 'qemu+ssh://%s/system' % dst_ip)
            dest_conn = libvirt.open(dst_host)
            if dest_conn is None:
                self.agent.logger.error('before_migrate_entity_actions()', 'XEN Plugin - Before Migration Source: '
                                                                          'Error on libvirt connection')
                exit(1)
            new_dom = dom.migrate(dest_conn,
                                  libvirt.VIR_MIGRATE_LIVE and libvirt.VIR_MIGRATE_PERSIST_DEST and libvirt.VIR_MIGRATE_NON_SHARED_DISK,
                                                                        entity.name, None, 0)
            if new_dom is None:
                self.agent.logger.error('before_migrate_entity_actions()', 'XEN Plugin - Before Migration Source: '
                                                                          'Migration failed')
                exit(1)

                self.agent.logger.info('before_migrate_entity_actions()', ' XEN Plugin - Before Migration Source: '
                                                                         'Migration succeeds')
            dest_conn.close()
            # #######################################

            # ## REMOVING AFTER MIGRATION
            self.agent.get_os_plugin().remove_know_host(dst_hostname)
            instance.on_stop()
            self.current_entities.update({entity_uuid: entity})

            return True

        :param entity_uuid:
        :param dst:
        :param instance_uuid:
        :return:
        """


    def after_migrate_entity_actions(self, entity_uuid, dst=False, instance_uuid=None):
        pass
        """
        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        entity = self.current_entities.get(entity_uuid, None)
        if entity is None:
            self.agent.logger.error('after_migrate_entity_actions()', 'XEN Plugin - Entity not exists')
            raise EntityNotExistingException('Enitity not existing',
                                             'Entity %s not in runtime %s' % (entity_uuid, self.uuid)))
        elif entity.get_state() != State.DEFINED:
            self.agent.logger.error('after_migrate_entity_actions()', 'XEN Plugin - Entity state is wrong, or transition not allowed')
            raise StateTransitionNotAllowedException('Entity is not in correct state',
                                                     'Entity %s is not in correct state' % entity.get_state()))
        else:
            if dst is True:

                instance = entity.get_instance(instance_uuid)
                '''
                Here the plugin also update to the current status, and remove unused keys
                '''
                self.agent.logger.info('after_migrate_entity_actions()', ' XEN Plugin - After Migration Destination: Updating state')
                instance.on_start()


                self.current_entities.update({entity_uuid: entity})

                uri = '%s/%s/%s/%s/%s' % (self.agent.dhome, self.HOME, entity_uuid,self.INSTANCE,instance_uuid))
                vm_info = json.loads(self.agent.dstore.get(uri))
                vm_info.pop('dst')
                vm_info.update({'status': 'run'})

                self.__update_actual_store_instance(entity_uuid,instance_uuid, vm_info)
                self.current_entities.update({entity_uuid: entity})

                return True
            else:
                '''
                Source node destroys all information about vm
                '''
                self.agent.logger.info('after_migrate_entity_actions()', ' XEN Plugin - After Migration Source: Updating state, destroy vm')
                self.__force_entity_instance_termination(entity_uuid,instance_uuid)
                return True

        :param entity_uuid:
        :param dst:
        :param instance_uuid:
        :return:
        """



    def __react_to_cache(self, uri, value, v):
        self.agent.logger.info('__react_to_cache()', ' XEN Plugin - React to to URI: {} Value: {} Version: {}'.format(uri, value, v))
        if uri.split('/')[-2] == 'entity':
            if value is None and v is None:
                self.agent.logger.info('__react_to_cache()', ' XEN Plugin - This is a remove for URI: {}'.format(uri))
                entity_uuid = uri.split('/')[-1]
                self.undefine_entity(entity_uuid)
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
        elif uri.split('/')[-2] == 'instance':
            if value is None and v is None:
                self.agent.logger.info('__react_to_cache()', ' XEN Plugin - This is a remove for URI: {}'.format(uri))
                instance_uuid = uri.split('/')[-1]
                entity_uuid = uri.split('/')[-3]
                self.__force_entity_instance_termination(entity_uuid,instance_uuid)
            else:
                instance_uuid = uri.split('/')[-1]
                entity_uuid = uri.split('/')[-3]
                value = json.loads(value)
                action = value.get('status')
                entity_data = value.get('entity_data')
                react_func = self.__react(action)
                if react_func is not None and entity_data is None:
                    react_func(entity_uuid, instance_uuid)
                elif react_func is not None:
                    entity_data.update({'entity_uuid': entity_uuid})
                    #if action == 'landing':
                    #    react_func(entity_data, dst=True, instance_uuid=instance_uuid)
                    #else:
                    #    react_func(entity_data, instance_uuid=instance_uuid)

    def __random_mac_generator(self):
        mac = [0x00, 0x16, 0x3e,
               random.randint(0x00, 0x7f),
               random.randint(0x00, 0xff),
               random.randint(0x00, 0xff)]
        return ':'.join(map(lambda x: '%02x' % x, mac))

    def __lookup_by_uuid(self, uuid):
        try:
            domains = self.conn.listAllDomains(0)
        except libvirt.libvirtError as err:
            self.__connect_to_hypervisor(self.hypervisor)
            domains = self.conn.listAllDomains(0)

        if len(domains) != 0:
            for domain in domains:
                if uuid == domain.UUIDString():
                    return domain
        else:
            return None

    def __wait_boot(self, filename, configured=False):
        """
        time.sleep(5)
        if configured:
            boot_regex = r"\[.+?\].+\[.+?\]:.+Cloud-init.+?v..+running.+'modules:final'.+Up.([0-9]*\.?[0-9]+).+seconds.\n"
        else:
            boot_regex = r'.+?login:()'

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
                    m = re.search(boot_regex, line))
                    if m:
                        found = m.group(1)
                        return found

        :param filename:
        :param configured:
        :return:
        """


    def __force_entity_instance_termination(self, entity_uuid, instance_uuid):
        if type(entity_uuid) == dict:
            entity_uuid = entity_uuid.get('entity_uuid')
        self.agent.logger.info('stop_entity()', ' XEN Plugin - Stop a VM uuid {}'.format(entity_uuid))
        entity = self.current_entities.get(entity_uuid, None)
        if entity is None:
            self.agent.logger.error('stop_entity()', 'XEN Plugin - Entity not exists')
            raise EntityNotExistingException('Enitity not existing', 'Entity {} not in runtime {}'.format(entity_uuid, self.uuid))
        else:
            if instance_uuid is None or not entity.has_instance(instance_uuid):
                self.agent.logger.error('run_entity()', 'XEN Plugin - Instance not found!!')
            else:
                instance = entity.get_instance(instance_uuid)
                if instance.get_state() == State.PAUSED:
                    self.resume_entity(entity_uuid, instance_uuid)
                    self.stop_entity(entity_uuid, instance_uuid)
                    self.clean_entity(entity_uuid, instance_uuid)
                if instance.get_state() == State.RUNNING:
                    self.stop_entity(entity_uuid, instance_uuid)
                    self.clean_entity(entity_uuid, instance_uuid)
                if instance.get_state() == State.CONFIGURED:
                    self.clean_entity(entity_uuid, instance_uuid)



    def __generate_dom_xml(self, instance):
        template_xml = self.agent.get_os_plugin().read_file(os.path.join(self.DIR, 'templates', 'vm.xml'))
        vm_xml = Environment().from_string(template_xml)
        vm_xml = vm_xml.render(name=instance.name, uuid=instance.uuid, memory=instance.ram,
                               cpu=instance.cpu, disk_image=instance.disk,
                               iso_image=instance.cdrom, networks=instance.networks)
        return vm_xml

    def __update_actual_store(self, uri, value):
        uri = '{}/{}/{}'.format(self.agent.ahome, self.HOME, uri)
        value = json.dumps(value)
        self.agent.astore.put(uri, value)

    def __update_actual_store_instance(self, entity_uuid, instance_uuid, value):
        uri = '{}/{}/{}/{}/{}'.format(self.agent.ahome, self.HOME, entity_uuid, self.INSTANCE, instance_uuid)
        value = json.dumps(value)
        self.agent.astore.put(uri, value)

    def __pop_actual_store(self, entity_uuid):
        uri = '{}/{}/{}'.format(self.agent.ahome, self.HOME, entity_uuid)
        self.agent.astore.remove(uri)


    def __pop_actual_store_instance(self, entity_uuid, instance_uuid):
        uri = '{}/{}/{}/{}/{}'.format(self.agent.ahome, self.HOME, entity_uuid, self.INSTANCE, instance_uuid)
        self.agent.astore.remove(uri)

    def __netmask_to_cidr(self, netmask):
        return sum([bin(int(x)).count('1') for x in netmask.split('.')])


    def __connect_to_hypervisor(self, address):
        self.conn = libvirt.open('xen+ssh://{}@{}'.format(self.user, address))


    def __execture_on_dom0(self, address, cmd):
        base_cmd = 'ssh {}@{} {}'.format(self.user, address, cmd)
        self.agent.get_os_plugin().execute_command(base_cmd, True)




    def __react(self, action):
        r = {
            'define': self.define_entity,
            'configure': self.configure_entity,
            'clean': self.clean_entity,
            'undefine': self.undefine_entity,
            'stop': self.stop_entity,
            'resume': self.resume_entity,
            'run': self.run_entity
            #'landing': self.migrate_entity,
            #'taking_off': self.migrate_entity
        }

        return r.get(action, None)
