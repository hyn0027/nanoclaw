"""Assemble, validate, and report the selected ontology graphs.

Pipeline (see ``cli.main``): read the manifest and discover graphs
(``catalog``), assemble the selected ones with provenance checks
(``assemble``), check structure and SHACL (``checks``), gather operations,
effects, and calls (``inventory``), print the report (``report``), and pass
touched resources to the optional LLM pair annotator (``annotation_input``).
"""
