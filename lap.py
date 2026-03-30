"""Compatibility shim for Ultralytics when the optional `lap` package is missing.

This project only needs the `lapjv` entrypoint used by ByteTrack. We provide a
SciPy-backed fallback so `model.track(...)` can run in environments where the
native `lap` wheel is unavailable.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import linear_sum_assignment

__version__ = "0.5.12"


def lapjv(cost_matrix, extend_cost=True, cost_limit=math.inf):
    """Approximate `lap.lapjv` using SciPy's Hungarian algorithm.

    Returns a tuple shaped like the real package:
    `(total_cost, row_to_col, col_to_row)`.
    Unmatched rows/columns are marked with `-1`.
    """
    matrix = np.asarray(cost_matrix, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("cost_matrix must be a 2D array")

    rows, cols = matrix.shape
    row_to_col = np.full(rows, -1, dtype=int)
    col_to_row = np.full(cols, -1, dtype=int)

    if matrix.size == 0:
        return 0.0, row_to_col, col_to_row

    assigned_rows, assigned_cols = linear_sum_assignment(matrix)

    total_cost = 0.0
    for row_idx, col_idx in zip(assigned_rows, assigned_cols):
        cost = matrix[row_idx, col_idx]
        if cost_limit is not None and np.isfinite(cost_limit) and cost > cost_limit:
            continue
        row_to_col[row_idx] = col_idx
        col_to_row[col_idx] = row_idx
        total_cost += float(cost)

    return total_cost, row_to_col, col_to_row
