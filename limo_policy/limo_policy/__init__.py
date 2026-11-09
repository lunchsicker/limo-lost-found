"""
Shared policy core for the LIMO lost-and-found robot (no ROS runtime dependency).

* ``specs``       loaders for config/robot_api.yaml, object_classes.yaml, search_locations_*.yaml
* ``paths``       where to find those files
* ``validator``   static check of policy code against the shared grammar
* ``interpreter`` exec-free reference interpreter for the grammar
* ``errors``      runtime exceptions (result codes of the ExecutePolicy contract)
* ``bundle``      PolicyBundle / HelperFunction (limo_llm -> limo_executor)
* ``call_graph``  helper call graph, cycle detection, dependency order
"""
