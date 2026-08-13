from __future__ import annotations

import pytest
from arrive90_models.calibration import (
    CalibrationCell,
    SigmoidCalibrator,
    calibrate_grid,
    fit_sigmoid_calibrator,
)


def test_sigmoid_calibration_preserves_endpoints_bounds_and_order() -> None:
    calibrator = SigmoidCalibrator(1.5, -0.2)
    values = calibrate_grid((0, 0.1, 0.5, 0.9, 1), calibrator)
    assert values[0] == 0
    assert values[-1] == 1
    assert values == tuple(sorted(values))
    assert all(0 <= value <= 1 for value in values)
    with pytest.raises(ValueError, match="strictly positive"):
        SigmoidCalibrator(0, 0)
    with pytest.raises(ValueError, match="inside"):
        calibrator.transform(1.1)


def test_calibrator_fit_is_deterministic_and_strictly_increasing() -> None:
    cells = (
        CalibrationCell(0.1, False, 1),
        CalibrationCell(0.3, False, 1),
        CalibrationCell(0.7, True, 1),
        CalibrationCell(0.9, True, 1),
    )
    first = fit_sigmoid_calibrator(cells)
    second = fit_sigmoid_calibrator(cells)
    assert first == second
    assert first.transform(0.2) < first.transform(0.8)
    with pytest.raises(ValueError, match="configuration"):
        fit_sigmoid_calibrator(())
    with pytest.raises(ValueError, match="interior"):
        fit_sigmoid_calibrator((CalibrationCell(0, False, 1),))
