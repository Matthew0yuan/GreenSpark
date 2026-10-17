"""Spike precursors: which signals public at forecast issue time help predict price spikes 0-48h ahead.

protocol  the forecast protocol: issue time, targets, label, periods, availability rules
sources   raw AEMO / Open-Meteo files -> point-in-time record tables (data/pit/*.parquet)
features  as-of joins -> the training table (one row per issue time x target interval)
models    B0-B5 walk-forward experiments, calibration, predictions
metrics   AP, Brier, budgets, event recall, block bootstrap
"""
