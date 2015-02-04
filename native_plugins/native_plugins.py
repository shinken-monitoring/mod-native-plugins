
import logging
import shlex

from Queue import Empty, Queue

import importlib


from shinken.basemodule import BaseModule
from shinkenplugins.plugin import NativePlugin, PluginResult


class NativePlugins(BaseModule):

    def __init__(self, mod_conf):
        super(NativePlugins, self).__init__(mod_conf)
        self.plugins = {}
        logg = self.logger = logging.getLogger()
        logg.setLevel(logging.DEBUG)
        logg.addHandler(logging.StreamHandler())

    def get_plugin(self, mod_name):
        plugin = self.plugins.get(mod_name, None)
        if not plugin:
            self.logger.info('Loading %r ..', mod_name)
            try:
                mod = importlib.import_module(mod_name)
            except ImportError as err:
                self.logger.exception("Could not import %r : %s", mod_name, err)
                raise
            class Native(NativePlugin, mod.Plugin):
                pass
            plugin = self.plugins[mod_name] = Native()
        return plugin


    def execute_check(self, check):
        cmd_items = shlex.split(check.command)
        try:
            plugin = self.get_plugin(cmd_items[0])
            res = plugin.execute(cmd_items[1:])
            self.logger.debug('%s : res=%s', check.command, res)
        except Exception as err:
            check.status = 3
            check.output = 'Unhandled exception: %s' % err
        else:
            #assert isinstance(res, PluginResult)
            self.logger.debug("vars(res)=%r", vars(res))
            self.logger.debug('res.perfdata=%r', res.perf_datas)
            check.exit_status = res.return_code
            check.output = res.output
            check.perf_data = '|'.join(map(str, res.perf_datas))
        check.status = 'done'
        self.out_queue.put(check)


    def work(self, *args):
        try:
            self.work_(*args)
        except Exception as err:
            self.logger.error('Fatal error during work: %s', err)

    def work_(self, in_queue, out_queue, control_queue):

        self.in_queue = in_queue
        self.out_queue = out_queue
        self.control_queue = control_queue

        while not self.interrupted:
            try:
                msg = in_queue.get(timeout=0.5)
            except Empty:
                continue
            check = msg.get_data()
            self.execute_check(check)
