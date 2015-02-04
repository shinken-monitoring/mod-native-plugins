
==================
mod-native-plugins
==================

Overview
========

What is it
----------

mod-native-plugins is a Shinken "module" (poller) to natively execute Shinken plugins which inherit the latest shinkenplugins.plugin.ShinkenPlugin class (shinkenplugins>=0.2.0).

Why use it
----------

Any command/plugin, whose load only is relatively high, will benefit from this.
The plugin is imported once, cached within the module, and re-used for all received Check instances.
