"""Evaluation harness for the extraction eval.

Separate from `services/` on purpose: nothing here runs in the API. This is the
measurement apparatus, and it is the part of the project that has to be right.
A bug in the pipeline produces a visible failure; a bug in here produces a
confidently wrong number.
"""
