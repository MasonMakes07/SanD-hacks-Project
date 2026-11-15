"""Bounded immutable server snapshots for explicit reassessment.

Receipts expire after one hour/restart. They are not history or persistent storage.
Clients can select IDs, never submit replacement source material.
"""
from collections import OrderedDict
from copy import deepcopy
from time import monotonic
from uuid import uuid4

TTL_SECONDS = 3600
MAX_RECORDS = 128
_analyses = OrderedDict()
_receipts = OrderedDict()


def _put(table, key, value):
    table[key] = (monotonic(), deepcopy(value))
    table.move_to_end(key)
    while len(table) > MAX_RECORDS:
        table.popitem(last=False)


def _get(table, key):
    row = table.get(key)
    if row is None:
        return None
    created, value = row
    if monotonic() - created > TTL_SECONDS:
        table.pop(key, None)
        return None
    return deepcopy(value)


def remember_analysis(result):
    if result.analysis_id:
        _put(_analyses, result.analysis_id, result)


def get_analysis(analysis_id):
    return _get(_analyses, analysis_id)


def issue_receipt(claim, sources, nodes, edges, provenance=None):
    token = uuid4().hex
    _put(_receipts, token, {'claim': claim, 'sources': sources, 'nodes': nodes, 'edges': edges, 'provenance': provenance})
    return token


def get_receipt(token):
    return _get(_receipts, token)
