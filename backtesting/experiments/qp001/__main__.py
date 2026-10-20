"""Keep BLAS single-threaded before importing numerical libraries."""
import os

for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"

from .runner import main

raise SystemExit(main())
