"""Instantiate the universal NanoClaw ontology for one concrete, running session.

Pipeline (see ``cli.main``):

1. ``collect``        facts from ``ncl``, the session mailbox, Docker, OneCLI, the checkout
                      (via the CLI wrappers in ``runtime``)
2. ``instantiate``    typed instances in a snapshot graph (``snapshot.Snapshot``)
3. ``conditions``     the model's requirements, settled for this session
4. ``capabilities``   native capabilities: available / conditional / unavailable
   ``tools``          provider built-in tools: enabled or not (lists read by ``provider_tools``)
5. ``routes``         network routes and management paths
6. ``modifications``  what can change inside the container, on the host, and remotely
7. ``report`` / ``output``  print the tables; check and write the snapshot
"""
