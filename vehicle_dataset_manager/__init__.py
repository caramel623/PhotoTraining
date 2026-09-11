"""Vehicle Dataset Manager.

A Windows-local tool that builds a manually-verifiable Vehicle/Motorcycle
Re-ID training dataset from historical speed-camera photos (ZIP/7Z).

Core principle: plate OCR is used as a *weak supervision / grouping* signal,
never as a Re-ID feature. The Re-ID model must learn vehicle appearance.
"""

__version__ = "0.0.3"
