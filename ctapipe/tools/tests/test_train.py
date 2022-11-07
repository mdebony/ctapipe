import pytest

from ctapipe.core import run_tool
from ctapipe.utils.datasets import resource_file


def test_train_energy_regressor(energy_regressor_path):
    from ctapipe.reco import EnergyRegressor

    EnergyRegressor.read(energy_regressor_path)


def test_train_particle_classifier(particle_classifier_path):
    from ctapipe.reco import ParticleClassifier

    ParticleClassifier.read(particle_classifier_path)


def test_too_few_events(tmp_path, dl2_shower_geometry_file, dl2_proton_geometry_file):
    from ctapipe.tools.train_energy_regressor import TrainEnergyRegressor
    from ctapipe.tools.train_particle_classifier import TrainParticleClassifier

    tool = TrainEnergyRegressor()
    config = resource_file("train_energy_regressor.yaml")
    out_file = tmp_path / "energy.pkl"

    with pytest.raises(ValueError, match="Too few events"):
        run_tool(
            tool,
            argv=[
                f"--input={dl2_shower_geometry_file}",
                f"--output={out_file}",
                f"--config={config}",
                "--log-level=INFO",
            ],
            raises=True,
        )

    tool = TrainParticleClassifier()
    config = resource_file("train_particle_classifier.yaml")
    out_file = tmp_path / "particle_classifier.pkl"

    with pytest.raises(ValueError, match="Only one class"):
        run_tool(
            tool,
            argv=[
                f"--signal={dl2_shower_geometry_file}",
                f"--background={dl2_proton_geometry_file}",
                f"--output={out_file}",
                f"--config={config}",
                "--log-level=INFO",
            ],
            raises=True,
        )


def test_cross_validation_results(tmp_path):
    from ctapipe.tools.train_energy_regressor import TrainEnergyRegressor
    from ctapipe.tools.train_particle_classifier import TrainParticleClassifier

    tool = TrainEnergyRegressor()
    config = resource_file("train_energy_regressor.yaml")
    out_file = tmp_path / "energy_.pkl"
    energy_cv_out_file = tmp_path / "energy_cv_results.h5"

    ret = run_tool(
        tool,
        argv=[
            "--input=dataset://gamma_diffuse_dl2_train_small.dl2.h5",
            f"--output={out_file}",
            f"--config={config}",
            f"--cv-output={energy_cv_out_file}",
            "--log-level=INFO",
        ],
    )
    assert ret == 0
    assert energy_cv_out_file.exists()

    tool = TrainParticleClassifier()
    config = resource_file("train_particle_classifier.yaml")
    out_file = tmp_path / "particle_classifier_.pkl"
    classifier_cv_out_file = tmp_path / "classifier_cv_results.h5"

    ret = run_tool(
        tool,
        argv=[
            "--signal=dataset://gamma_diffuse_dl2_train_small.dl2.h5",
            "--background=dataset://proton_dl2_train_small.dl2.h5",
            f"--output={out_file}",
            f"--config={config}",
            f"--cv-output={classifier_cv_out_file}",
            "--log-level=INFO",
        ],
    )
    assert ret == 0
    assert classifier_cv_out_file.exists()
