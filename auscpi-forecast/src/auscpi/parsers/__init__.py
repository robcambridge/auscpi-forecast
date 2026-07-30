"""Parsers turn raw snapshots into tidy frames.

Deliberately separate from collectors/: a collector must stay dumb so that when a
parser turns out to be wrong — and one will — history can be reprocessed from
data/raw instead of lost. Everything here is a pure function of a stored payload,
so re-running the build on old snapshots is always safe.
"""
