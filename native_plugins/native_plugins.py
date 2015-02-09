# -*- coding: utf-8 -*-

from Queue import Empty
import logging
import os
import shlex
import sys
import time
import threading

import importlib

from shinken.basemodule import BaseModule
from shinkenplugins.plugin import ShinkenPlugin, NativePlugin


class WorkerThreadCtx(object):
    def __init__(self):
        self.thread = None
        self.check = None


class PluginCtx(object):
    def __init__(self, mod, base, execute, mod_ts):
        self.mod = mod
        self.base = base
        self.execute = execute
        self.mod_ts = mod_ts


class NativePluginsModule(BaseModule):


    def get_worker_threads(self, mod_conf, default=4):
        try:
            if hasattr(mod_conf, 'worker_threads'):
                return int(mod_conf.worker_threads)
        except Exception as err:
            self.logger.warning('worker_threads not parsable as int: %s', err)
        return default

    def set_log_level(self, mod_conf, default=logging.INFO):
        try:
            self.logger.setLevel(getattr(mod_conf, 'logging_level', default))
            return
        except Exception as err:
            self.logger.warning('logging_level not usable as logLevel: %s', err)
        self.logger.setLevel(default)

    def __init__(self, mod_conf):
        super(NativePluginsModule, self).__init__(mod_conf)
        self.plugins = {}
        # key == plugin "base" (module name or script path),
        # value == PluginCtx instance

        self.threads = {}
        # key == thread.ident
        # value == WorkerThreadCtx instance
        self.n_threads = self.get_worker_threads(mod_conf)

        self.lock = threading.Lock()  # only used to load/import plugins

        logger = self.logger = logging.getLogger('shinken.plugins')
        self.set_log_level(mod_conf)

        if not logger.handlers:
            logger.addHandler(logging.StreamHandler())

    def load_plugin(self, plugin_name):
        self.logger.info('Loading %r ..', plugin_name)
        if os.path.isfile(plugin_name) and plugin_name.endswith('.py'):
            plugin_dir = os.path.dirname(plugin_name)
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)
            plugin_import = os.path.basename(plugin_name)
        else:  # assume it's a python module name (package.subpackage.module)
            plugin_import = plugin_name

        before_mods = sys.modules.keys()
        try:
            plugin_mod = importlib.import_module(plugin_import)
        except ImportError as err:
            self.logger.exception("Could not import %r : %s", plugin_name, err)
            raise
        finally:
            to_del = []
            for mod in sys.modules:
                if mod not in before_mods:
                    to_del.append(mod)
            for mod in to_del:
                del sys.modules[mod]

        if (hasattr(plugin_mod, 'Plugin')
            and isinstance(plugin_mod.Plugin, type)
            and issubclass(plugin_mod.Plugin, ShinkenPlugin)
        ):
            class Native(NativePlugin, plugin_mod.Plugin):
                pass
            execute = Native().execute
        elif hasattr(plugin_mod, 'main') and callable(plugin_mod.main):
            execute = plugin_mod.main
        else:
            raise Exception('Not a usable native plugin: no Plugin class nor main() function')

        return PluginCtx(plugin_mod, plugin_name, execute, os.stat(plugin_mod.__file__).st_mtime)


    def get_plugin(self, plugin_name):
        '''
        :param plugin_name:
        :return:    The plugin context
        :rtype:     PluginCtx
        '''
        plugin = self.plugins.get(plugin_name, None)
        if not plugin:
            with self.lock:
                plugin = self.plugins.get(plugin_name, None)
                if not plugin:
                    plugin = self.load_plugin(plugin_name)
                    self.plugins[plugin_name] = plugin
        return plugin

    def execute_check(self, check):
        '''
        :param check:
        :type check:    shinken.check.Check
        '''
        check_items = shlex.split(check.command)
        check_base = check_items[0]
        try:
            plugin = self.get_plugin(check_base)
            check.check_time = time.time()
            res = plugin.execute(check_items[1:])
            self.logger.debug('%s : res=%s', check.command, res)
        except Exception as err:
            check.status = 3
            check.output = '%s: %s' % (check_base, err)
        else:
            check.exit_status = res.return_code
            check.output = res.output
            check.perf_data = '|'.join(map(str, res.perf_datas))
        check.execution_time = time.time() - check.check_time
        check.status = 'done'
        self.out_queue.put(check)

    def thread_run(self, ctx):
        ''' Main worker thread function '''
        self.logger.debug('%s : now waiting for incoming checks..', ctx.thread)
        while not self.interrupted:
            try:
                msg = self.in_queue.get(timeout=0.5)
            except Empty:
                continue
            check = msg.get_data()
            ctx.check = check
            self.execute_check(check)
            ctx.check = None

    def add_new_thread(self):
        ctx = WorkerThreadCtx()
        thread = threading.Thread(target=self.thread_run, args=(ctx,))
        thread.daemon = True
        ctx.thread = thread
        self.threads[thread] = ctx
        thread.start()

    def work(self, in_queue, out_queue, control_queue):
        self.in_queue = in_queue
        self.out_queue = out_queue
        # actually unused: self.control_queue = control_queue
        # BaseModule._main() setup different things and then will call our main().
        self._main()

    def main(self, *args):
        self.logger.info('Using %s threads ..', self.n_threads)
        try:
            self.real_main(*args)
        except Exception as err:
            self.logger.exception('Fatal error: %s', err)
            self.interrupted = True
        finally:
            for ctx in self.threads.values():
                ctx.thread.join()
            self.logger.info('%s: Terminated, exiting..', self.get_name())

    def real_main(self):

        check_threads_every = 5
        # make sure to directly create the threads:
        next_check_threads = 0

        check_plugins_timestamps_every = 60
        next_check_plugins_timestamp = time.time() + check_plugins_timestamps_every

        while not self.interrupted:

            time.sleep(1)

            if time.time() > next_check_threads:
                next_check_threads += check_threads_every
                for ctx in self.threads.values():
                    thread = ctx.thread
                    if thread.isAlive():
                        pass
                        # TODO:
                        # should we verify that the thread isn't executing a check for too long ?
                        # but then if yes : what to do if the check is in that case ??
                        # Because even in Python a thread can't necessarily always be safely
                        # cancelled..
                    else:
                        if self.interrupted:
                            return
                        self.logger.warning('Thread %s exited ; check=%r', thread, ctx.check)
                        thread.join()
                        del self.threads[thread]

                for _ in range(self.n_threads - len(self.threads)):
                    self.add_new_thread()

            if time.time() > next_check_plugins_timestamp:
                next_check_plugins_timestamp += check_plugins_timestamps_every
                to_reload = []
                with self.lock:
                    for name, plugin in self.plugins.items():
                        new_ts = os.stat(plugin.mod.__file__).st_mtime
                        if new_ts > plugin.mod_ts:
                            to_reload.append(name)
                    for name in to_reload:
                        del self.plugins[name]
                        # that will so force us to import it again on next check received.
                if to_reload:
                    self.logger.info("Following Plugins have been modified, "
                                     "I've dropped them from my cache for reload.. plugins=%s",
                                     to_reload)


    def do_stop(self):
        self.interrupted = True  # just to make sure, otherwise we could wait forever on our threads
        self.logger.debug('Waiting on my threads..')
        for ctx in self.threads.values():
            ctx.thread.join()
        self.logger.info('all threads joined.')
        super(NativePluginsModule, self).do_stop()
