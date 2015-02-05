'''
Shinken Native-Plugin Module.
'''


from .native_plugins import NativePluginsModule


properties = {
    'daemons':          ['poller', 'reactionner'],
    'type':             'native_plugins',
    'phases':           ['running'],
    'worker_capable':   True,
}


def get_instance(mod_conf):
    return NativePluginsModule(mod_conf)
