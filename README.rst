
==================
mod-native-plugins
==================

Overview
========

What is it
----------

mod-native-plugins is a Shinken "module" (poller) to natively execute Shinken Python plugins.

For doing so the plugin only need to either:

a) inherit the latest shinkenplugins.plugin.ShinkenPlugin class (shinkenplugins>=0.2.0)
b) declare a main() method, taking a single argument being the list of arguments to give the plugin.


Why use it
----------

Any command/plugin, whose load/import only is relatively high, will benefit from this.
The plugin is loaded/imported once, cached within the poller module, and re-used for each received Check instance.
