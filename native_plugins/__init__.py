
from .native_plugins import NativePlugins


properties = {
    'daemons':          ['poller', 'reactionner'],
    'type':             'native_plugins',
    'phases':           ['running'],
    'worker_capable':   True,
}



def get_instance(mod_conf):
    return NativePlugins(mod_conf)
