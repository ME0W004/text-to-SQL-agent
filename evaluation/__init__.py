"""Offline-capable evaluation harness for the Text-to-SQL agent."""

from .adapters import DatasetAdapter, EvaluationAgentAdapter, load_dataset
from .models import CanonicalCase, EvaluationResponse, EvaluationRun

__all__ = [
    "CanonicalCase",
    "DatasetAdapter",
    "EvaluationAgentAdapter",
    "EvaluationResponse",
    "EvaluationRun",
    "load_dataset",
]
