from fog05.interfaces.Store import *
from fog05.DController import *
import fnmatch
from threading import Thread
import json

class DStore(Store):

    def __init__(self, store_id, root, home, cache_size):
        super(DStore, self).__init__()
        self.root = root
        self.home = home
        self.store_id = store_id
        self.__store = {} # This stores URI whose prefix is **home**
        self.discovered_stores = [] # list of discovered stores not including self
        self.__cache_size = cache_size
        self.__local_cache = {} # this is a cache that stores up
                                # to __cache_size entry for URI whose prefix is not **home**
        self.__observers = {}
        self.__controller = DController(self)

        self.__controller.start()

        '''
        @GB: As discussed with Erik and Angelo, maybe can be better to have 2 `store` for local information
        one with desidered state (that can be written by all nodes and readed only by the owner node)
        and one with actual state (that can be written only by the owner node and readed by all nodes)
        
        This means that plugins and agent works to make the actual state match the desidered state,
        this is also very useful in the case we want to know if some entity/plugin/whatever state changed.
        
        So this means we should have two or more different put,get, dput, and observer
        
        
        +-------------------------------------------------------------------+
        |                                                  Agent1           |
        |                                                                   |
        |    Desidered Store                        Actual Store            |
        |    +-------+                               +-------+              |
        |    |       |                               |       |              |
        |    |       |        +---------------+      |       |              |
        |    |       +------->| Plugins/Agent |----->+       |              |
        |    |       |        +---------------+      |       |              |
        |    |       |                               |       |              |
        |    |       |                               |       |              |
        |    +---^---+                               +---+---+              |
        |        |                                       |                  |
        |        |                                       |                  |
        +--------|---------------------------------------|------------------+
                 |                                       |
                 | put(dfos:/s1/a1/...)                  |   get(afos://s1/a1/....)
                 |                                       |
         +-------+---------------------------------------V--------------------+
         |                                                  Agent2           |
         |                                                                   |
         |                                                                   |
         |    +-------+                               +-------+              |
         |    |       |                               |       |              |
         |    |       |        +---------------+      |       |              |
         |    |       |------->| Plugins/Agent |----->|       |              |
         |    |       |        +---------------+      |       |              |
         |    |       |                               |       |              |
         |    |       |                               |       |              |
         |    +-------+                               +-------+              |
         |    Desidered Store                        Actual Store            |
         |                                                                   |
         +-------------------------------------------------------------------+


        So the data flow should follow this diagram.
        
        
        This semplify a lot waiting for some entity/plugin to state changed
        eg. someone decide to deploy a vm, so send a define, then a configure
        during configuring the actual state remain to defined until the configuration is done
        then the kvm plugin update the state in the `Actual Store`, and so someone that deployed the vm
        can now be sure that is configured and can send a state transition to run, this is the same case
        as above, sto the actual state remains to configured until kvm is sure that the vm is 
        started and ready to serve (so can also populate monitoring information immediately after
        the state transition.
        
        So someone that want to be updated about state transition can simply register an observer
        or can do a busy wait by doing some get (beacuse in this case get and observer are linked only 
        to the actual state)
        
        '''


    def is_stored_value(self, uri):
        if uri.startswith(self.home):
            return True
        else:
            l = len(self.home)
            p = uri[:l]
            return fnmatch.fnmatch(self.home, p)


    def is_cached_value(self, uri):
        return  not self.is_stored_value(uri)


    def get_version(self, uri):
        version = None
        v = None
        if self.is_stored_value(uri):
            if uri in self.__store:
                v = self.__store[uri]
        else:
            if uri in self.__local_cache:
                v = self.__local_cache[uri]

        if v is not None:
            version = v[1]

        return version

    def get_value(self, uri):
        v = None
        if self.is_stored_value(uri):
            if uri in self.__store:
                v = self.__store[uri]
        else:
            if uri in self.__local_cache:
                v = self.__local_cache[uri]
        return v

    def next_version(self, uri):
        nv = 0
        v = self.get_version(uri)
        if v is not None:
            nv = v + 1

        return nv

    def __unchecked_store_value(self, uri, value, version):
        if self.is_stored_value(uri):
            self.__store[uri] = (value, version)
        else:
            self.__local_cache[uri] = (value, version)


    def update_value(self, uri, value, version):
        succeeded = False

        current_version = self.get_version(uri)
        #print('Updating URI: {0} to value: {1} and version = {2} -- older version was : {3}'.format(uri, value, version, current_version))
        if current_version != None:
            if current_version < version:
                self.__unchecked_store_value(uri, value, version)
                succeeded = True
        else:
            self.__unchecked_store_value(uri, value, version)
            succeeded = True


        return succeeded

    def notify_observers(self, uri, value, v):
        # AC: Should not use a separate thread of each observer... This is going to result in a
        #     few DDS writes which are non-blocking and thus not so useful to start a separate thread
        for key in list(self.__observers.keys()):
            if fnmatch.fnmatch(uri, key):
                self.__observers.get(key)(uri, value, v)
                #Thread(target=self.__observers.get(key), args=(uri, value, v)).start()

    def put(self, uri, value):
        v = self.get_version(uri)
        if v == None:
            v = 0
        else:
            v = v + 1
        self.update_value(uri, value, v)

        # It is always the observer that inserts data in the cache
        self.__controller.onPut(uri, value, v)
        self.notify_observers(uri, value, v)


    def pput(self, uri, value):
        v = self.next_version(uri)
        self.__unchecked_store_value(uri, value, v)
        self.__controller.onPput(uri, value, v)
        self.notify_observers(uri, value, v)


    def conflict_handler(self, action):
        pass

    def dput(self, uri, values = None):

        #print('>>> dput >>> URI: {0} VALUE: {1}'.format(uri, values))
        uri_values = ''
        if values is None:
            ##status=run&entity_data.memory=2GB
            uri = uri.split('#')
            uri_values = uri[-1]
            uri = uri[0]

        data = self.get(uri)
        #print('>>> dput resolved {0} to {1}'.format(uri, data))
        #print('>>> dput resolved type is {0}'.format(type(data)))
        version = 0
        if data is None or data == '':
            data = {}
        else:
            data = json.loads(data)
            version = self.next_version(uri)

        # version = self.next_version(uri)
        # data = {}
        # for key in self.__local_cache:
        #     if fnmatch.fnmatch(key, uri):
        #         data = json.loads(self.__local_cache.get(key)[0])
        #
        #
        # # @TODO: Need to resolve this miss
        # if len(data) == 0:
        #     data = self.get(uri)
        #     if data is None:
        #         return
        #     else:
        #         self.__unchecked_store_value(uri, data, self.next_version(uri))
        #         for key in self.__local_cache:
        #             if fnmatch.fnmatch(key, uri):
        #                 data = json.loads(self.__local_cache.get(key)[0])
        #         version = self.next_version(uri)

        #print('>>>VALUES {0} '.format(values))
        #print('>>>VALUES TYPE {0} '.format(type(values)))
        if values is None:
            uri_values = uri_values.split('&')
            #print('>>>URI VALUES {0} '.format(uri_values))
            for tokens in uri_values:
                #print('INSIDE for tokens {0}'.format(tokens))
                v = tokens.split('=')[-1]
                k = tokens.split('=')[0]
                #if len(k.split('.')) < 2:
                #    data.update({k: v})
                #    print('>>>merged data  {0} '.format(data))
                #else:
                d = self.dot2dict(k, v)

                data = self.data_merge(data, d)
                #print('>>>merged data  {0} '.format(data))
        else:
            jvalues = json.loads(values)
            ##print('dput delta value = {0}, data = {1}'.format(jvalues, data))
            data = self.data_merge(data, jvalues)

        ##print('dput merged data = {0}'.format(mdata))

        value = json.dumps(data)
        self.__unchecked_store_value(uri, value , version)
        self.__controller.onDput(uri, value, version)
        self.notify_observers(uri, value, version)
        return True





    def observe(self, uri, action):
        self.__observers.update({uri: action})

    def remove(self, uri):
        self.__controller.onRemove(uri)
        try:
            self.__local_cache.pop(uri)
        except KeyError:
            #print('>>>> KeyError on pop')
            pass

    def remote_remove(self, uri):
        try:
            self.__local_cache.pop(uri)
            self.notify_observers(uri, None, None)
        except:
            pass

    def get(self, uri):
        v = self.get_value(uri)
        if v == None:
            self.__controller.onMiss()
            #print('Resolving: {0}'.format(uri))
            rv = self.__controller.resolve(uri)
            if rv != None:
                #print('URI: {0} was resolved to val = {1} and ver = {2}'.format(uri, rv[0], rv[1]))
                self.update_value(uri, rv[0], rv[1])
                self.notify_observers(uri, rv[0], rv[1])
                return rv[0]
            else:
                return None
        else:
            return v[0]

    def getAll(self, uri):
        xs = []
        for k,v in self.__store.items():
            if fnmatch.fnmatch(k, uri):
                xs.append((k, v[0], v[1]))
        for k,v in self.__local_cache.items():
            if fnmatch.fnmatch(k, uri):
                xs.append((k, v[0], v[1]))

        #print('>>>>>> getAll({0}) = {1}'.format(uri, xs))
        return xs

    def resolveAll(self, uri):
        xs = self.__controller.resolveAll(uri)
        #print(' Resolved list = {0}'.format(xs))
        ys  = self.getAll(uri)
        ks = []
        for x in xs:
            ks.append(x[0])
            #print('resolved key = {0}'.format(x[0]))

        for y in ys:
            #print('merging key: {0}'.format(y[0]))
            if y[0] not in ks:
                #print('Key is not present... Appending')
                xs.append(y)

        return xs

    def miss_handler(self, action):
        pass

    def iterate(self):
        pass

    def __str__(self):
        ret = ''
        for key, value in self.__local_cache.items():
            ret = str('%s%s' % (ret,str('Key: %s - Value: %s\n' % (key, value))))

        return ret

    #convert dot notation to a dict
    def dot2dict(self, dot_notation, value=None):
        ld = []

        tokens = dot_notation.split('.')
        n_tokens = len(tokens)
        for i in range(n_tokens, 0, -1):
            if i == n_tokens and value is not None:
                ld.append({tokens[i - 1]: value})
            else:
                ld.append({tokens[i - 1]: ld[-1]})

        return ld[-1]

    def data_merge(self, base, updates):
        ##print('data_merge base = {0}, updates= {1}'.format(base, updates))
        ##print('type of base  = {0} update = {1}'.format(type(base), type(updates)))
        if base is None or isinstance(base, int) or isinstance(base, str) or isinstance(base, float):
            base = updates
        elif isinstance(base, list):
            if isinstance(updates, list):
                names = [x.get('name') for x in updates]
                item_same_name = [item for item in base if item.get('name') in [x.get('name') for x in updates]]
                ##print(names)
                ##print(item_same_name)
                if all(isinstance(x, dict) for x in updates) and len(
                        [item for item in base if item.get('name') in [x.get('name') for x in updates]]) > 0:
                    for e in base:
                        for u in updates:
                            if e.get('name') == u.get('name'):
                                self.data_merge(e, u)
                else:
                    base.extend(updates)
            else:
                base.append(updates)
        elif isinstance(base, dict):
            if isinstance(updates, dict):
                for k in updates.keys():
                    if k in base.keys():
                        base.update({k: self.data_merge(base.get(k), updates.get(k))})
                    else:
                        base.update({k: updates.get(k)})
        return base

    # def data_merge(self, base, updates):
    #     if base is None or isinstance(base, int) or isinstance(base, str) or isinstance(base, float):
    #         base = updates
    #     elif isinstance(base, list):
    #         if isinstance(updates, list):
    #             names = [x.get('name') for x in updates]
    #             item_same_name = [item for item in base if item.get('name') in [x.get('name') for x in updates]]
    #             if all(isinstance(x, dict) for x in updates) and len(
    #                     [item for item in base if item.get('name') in [x.get('name') for x in updates]]) > 0:
    #                 for e in base:
    #                     for u in updates:
    #                         if e.get('name') == u.get('name'):
    #                             self.data_merge(e, u)
    #             else:
    #                 base.extend(updates)
    #         else:
    #             base.append(updates)
    #     elif isinstance(base, dict):
    #         if isinstance(updates, dict):
    #             for k in updates.keys():
    #                 if k in base.keys():
    #                     base.update({k: self.data_merge(base.get(k), updates.get(k))})
    #                 else:
    #                     base.update({k: updates.get(k)})
    #     return base

    def on_store_discovered(self, sid):
        raise NotImplemented

    def on_store_disappeared(self, sid):
        raise NotImplemented
#
# class DDSObserver(Observer):
#
#     def onRemove(self, uri):
#         #print('Observer onRemove Called')
#
#     def onConflict(self):
#         #print('Observer onConflict Called')
#
#     def onDput(self, uri):
#         #print('Observer onDput Called')
#
#     def onPput(self, uri, value):
#         #print('Observer onPput Called')
#
#     def onMiss(self):
#         #print('Observer onMiss Called')
#
#     def onGet(self, uri):
#         #print('Observer onGet Called')
#
#     def onObserve(self, uri, action):
#         #print('Observer onObserve Called')
#
#     def onPut(self, uri, value):
#         #print('Observer onPut Called')
#
#
# class DDSController(Controller):
#
#     def __init__(self, cache):
#         super(DDSController, self).__init__(cache)
#
#     def start(self):
#         #print('Controller start Called')
#
#     def stop(self):
#         #print('Controller stop Called')
#
#     def resume(self):
#         #print('Controller resume Called')
#
#     def pause(self):
#         #print('Controller pause Called')
#
