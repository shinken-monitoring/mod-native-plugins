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
        # plugins :
        # key == plugin "base" (module name or script path),
        # value == the plugin execute method/function.

        self.threads = {}
        # threads:
        # key == thread.ident
        # value == WorkerThreadCtx instance
        self.n_threads = self.get_worker_threads(mod_conf)

        self.lock = threading.Lock()  # only used to import plugins

        logger = self.logger = logging.getLogger('shinken.plugins')
        self.set_log_level(mod_conf)

        if not logger.handlers:
            logger.addHandler(logging.StreamHandler())

    def load_plugin(self, plugin_name):
        self.logger.info('Loading %r ..', plugin_name)
        if os.path.isfile(plugin_name):
            plugin_dir = os.path.dirname(plugin_name)
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)
            plugin_import = os.path.basename(plugin_name)
        else:  # assume it's a python module name (package.subpackage.module)
            plugin_import = plugin_name
        try:
            mod = importlib.import_module(plugin_import)
        except ImportError as err:
            self.logger.exception("Could not import %r : %s", plugin_name, err)
            raise

        if (hasattr(mod, 'Plugin')
            and isinstance(mod.Plugin, type)
            and issubclass(mod.Plugin, ShinkenPlugin)
        ):
            class Native(NativePlugin, mod.Plugin):
                pass
            execute = Native().execute
        elif hasattr(mod, 'main') and callable(mod.main):
            execute = mod.main
        else:
            raise Exception('Not a usable native plugin: no Plugin class nor main() function')
        return execute

    def get_plugin(self, plugin_name):
        '''
        :param plugin_name:
        :return:    The plugin execute method.
        :rtype:     callable
        '''
        with self.lock:
            execute = self.plugins.get(plugin_name, None)
            if not execute:
                execute = self.load_plugin(plugin_name)
                self.plugins[plugin_name] = execute
        return execute

    def execute_check(self, check):
        check_items = shlex.split(check.command)
        check_base = check_items[0]
        try:
            plugin_exec = self.get_plugin(check_base)
            check.check_time = time.time()
            res = plugin_exec(check_items[1:])
            self.logger.debug('%s : res=%s', check.command, res)
        except Exception as err:
            check.status = 3
            check.output = '%s: %s' % (check_base, err)
        else:
            #assert isinstance(res, PluginResult)
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

        while not self.interrupted:

            time.sleep(1)

            for ctx in self.threads.values():
                thread = ctx.thread
                if thread.isAlive():
                    pass
                    # TODO: should we verify that the thread isn't executing a check for too long ?
                    # but then if yes : what to do if the check is in that case ??
                    # because even in Python a thread can't necessarily always be safely cancelled..
                else:
                    if self.interrupted:
                        return
                    self.logger.warning('Thread %s exited ; check=%r', thread, ctx.check)
                    thread.join()
                    del self.threads[thread]

            for _ in range(self.n_threads - len(self.threads)):
                self.add_new_thread()



