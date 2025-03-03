"""Module containing classes related to event loading and preprocessing"""

from pathlib import Path

import astropy.units as u
import numpy as np
from astropy.coordinates import AltAz, SkyCoord
from astropy.table import Column, QTable, Table, vstack
from pyirf.simulations import SimulatedEventsInfo
from pyirf.spectral import (
    DIFFUSE_FLUX_UNIT,
    POINT_SOURCE_FLUX_UNIT,
    PowerLaw,
    calculate_event_weights,
)
from pyirf.utils import calculate_source_fov_offset, calculate_theta
from tables import NoSuchNodeError

from ..compat import COPY_IF_NEEDED
from ..containers import CoordinateFrameType
from ..coordinates import NominalFrame
from ..core import Component
from ..core.traits import Bool, Unicode
from ..io import TableLoader
from .cuts import EventQualitySelection, EventSelection
from .spectra import SPECTRA, Spectra

__all__ = ["EventLoader", "EventPreprocessor"]


class EventPreprocessor(Component):
    """Defines pre-selection cuts and the necessary renaming of columns."""

    classes = [EventQualitySelection, EventSelection]

    energy_reconstructor = Unicode(
        default_value="RandomForestRegressor",
        help="Prefix of the reco `_energy` column",
    ).tag(config=True)

    geometry_reconstructor = Unicode(
        default_value="HillasReconstructor",
        help="Prefix of the `_alt` and `_az` reco geometry columns",
    ).tag(config=True)

    gammaness_classifier = Unicode(
        default_value="RandomForestClassifier",
        help="Prefix of the classifier `_prediction` column",
    ).tag(config=True)

    irf_pre_processing = Bool(
        default_value=True,
        help="If true the pre processing assume the purpose is for IRF production, if false, for DL3 production",
    ).tag(config=False)

    optional_dl3_columns = Bool(
        default_value=False, help="If true add optional columns to produce file"
    ).tag(config=True)

    raise_error_for_optional = Bool(
        default_value=True,
        help="If true will raise error in the case optional column are missing",
    ).tag(config=True)

    def __init__(
        self, quality_selection_only: bool = True, config=None, parent=None, **kwargs
    ):
        super().__init__(config=config, parent=parent, **kwargs)
        if quality_selection_only:
            self.event_selection = EventQualitySelection(parent=self)
        else:
            self.event_selection = EventSelection(parent=self)

    def normalise_column_names(self, events: Table) -> QTable:
        if self.irf_pre_processing and events["subarray_pointing_lat"].std() > 1e-3:
            raise NotImplementedError(
                "No support for making irfs from varying pointings yet"
            )
        if any(events["subarray_pointing_frame"] != CoordinateFrameType.ALTAZ.value):
            raise NotImplementedError(
                "At the moment only pointing in altaz is supported."
            )

        keep_columns = [
            "obs_id",
            "event_id",
        ]
        if self.irf_pre_processing:
            keep_columns += [
                "true_energy",
                "true_az",
                "true_alt",
            ]
        else:
            keep_columns += [
                "time",
            ]
            if self.optional_dl3_columns:
                keep_columns += [
                    "tels_with_trigger",
                ]

        rename_from = [
            f"{self.energy_reconstructor}_energy",
            f"{self.geometry_reconstructor}_az",
            f"{self.geometry_reconstructor}_alt",
            f"{self.gammaness_classifier}_prediction",
            "subarray_pointing_lat",
            "subarray_pointing_lon",
        ]
        rename_to = [
            "reco_energy",
            "reco_az",
            "reco_alt",
            "gh_score",
            "pointing_alt",
            "pointing_az",
        ]
        if self.optional_dl3_columns:
            rename_from_optional = [
                f"{self.energy_reconstructor}_energy_uncert",
                f"{self.geometry_reconstructor}_ang_distance_uncert",
                f"{self.geometry_reconstructor}_core_x",
                f"{self.geometry_reconstructor}_core_y",
                f"{self.geometry_reconstructor}_core_uncert_x",
                f"{self.geometry_reconstructor}_core_uncert_y",
                f"{self.geometry_reconstructor}_h_max",
                f"{self.geometry_reconstructor}_h_max_uncert",
            ]
            rename_to_optional = [
                "reco_energy_uncert",
                "reco_dir_uncert",
                "reco_core_x",
                "reco_core_y",
                "reco_core_uncert_x",
                "reco_core_uncert_y",
                "reco_h_max",
                "reco_h_max_uncert",
            ]
            if not self.raise_error_for_optional:
                for i, c in enumerate(rename_from_optional):
                    if c not in events.colnames:
                        self.log.warning(
                            f"Optional column {c} is missing from the DL2 file."
                        )
                    else:
                        rename_from.append(rename_from_optional[i])
                        rename_to.append(rename_to_optional[i])
            else:
                rename_from += rename_from_optional
                rename_to += rename_to_optional

        keep_columns.extend(rename_from)
        for c in keep_columns:
            if c not in events.colnames:
                raise ValueError(
                    "Input files must conform to the ctapipe DL2 data model. "
                    f"Required column {c} is missing."
                )

        renamed_events = QTable(events, copy=COPY_IF_NEEDED)
        renamed_events.rename_columns(rename_from, rename_to)
        return renamed_events

    def keep_nescessary_columns_only(self, events: Table) -> QTable:
        keep_columns = [
            "obs_id",
            "event_id",
            "true_energy",
            "true_az",
            "true_alt",
            "reco_energy",
            "reco_az",
            "reco_alt",
            "reco_fov_lat",
            "reco_fov_lon",
            "pointing_az",
            "pointing_alt",
            "theta",
            "true_source_fov_offset",
            "reco_source_fov_offset",
            "gh_score",
            "weight",
        ]
        return QTable(events[keep_columns], copy=COPY_IF_NEEDED)

    def make_empty_table(self) -> QTable:
        """
        This function defines the columns later functions expect to be present
        in the event table.
        """
        columns = [
            Column(name="obs_id", dtype=np.uint64, description="Observation block ID"),
            Column(name="event_id", dtype=np.uint64, description="Array event ID"),
            Column(
                name="true_energy",
                unit=u.TeV,
                description="Simulated energy",
            ),
            Column(
                name="true_az",
                unit=u.deg,
                description="Simulated azimuth",
            ),
            Column(
                name="true_alt",
                unit=u.deg,
                description="Simulated altitude",
            ),
            Column(
                name="reco_energy",
                unit=u.TeV,
                description="Reconstructed energy",
            ),
            Column(
                name="reco_az",
                unit=u.deg,
                description="Reconstructed azimuth",
            ),
            Column(
                name="reco_alt",
                unit=u.deg,
                description="Reconstructed altitude",
            ),
            Column(
                name="reco_fov_lat",
                unit=u.deg,
                description="Reconstructed field of view lat",
            ),
            Column(
                name="reco_fov_lon",
                unit=u.deg,
                description="Reconstructed field of view lon",
            ),
            Column(name="pointing_az", unit=u.deg, description="Pointing azimuth"),
            Column(name="pointing_alt", unit=u.deg, description="Pointing altitude"),
            Column(
                name="theta",
                unit=u.deg,
                description="Reconstructed angular offset from source position",
            ),
            Column(
                name="true_source_fov_offset",
                unit=u.deg,
                description="Simulated angular offset from pointing direction",
            ),
            Column(
                name="reco_source_fov_offset",
                unit=u.deg,
                description="Reconstructed angular offset from pointing direction",
            ),
            Column(
                name="gh_score",
                unit=u.dimensionless_unscaled,
                description="prediction of the classifier, defined between [0,1],"
                " where values close to 1 mean that the positive class"
                " (e.g. gamma in gamma-ray analysis) is more likely",
            ),
            Column(
                name="weight",
                unit=u.dimensionless_unscaled,
                description="Event weight",
            ),
        ]

        return QTable(columns)


class EventLoader(Component):
    """
    Contains functions to load events and simulation information from a file
    and derive some additional columns needed for irf calculation.
    """

    classes = [EventPreprocessor]

    def __init__(
        self,
        file: Path,
        target_spectrum: Spectra = None,
        quality_selection_only: bool = True,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.epp = EventPreprocessor(
            quality_selection_only=quality_selection_only, parent=self
        )
        if target_spectrum is not None:
            self.target_spectrum = SPECTRA[target_spectrum]
        else:
            self.target_spectrum = None
        self.file = file

    def load_preselected_events(self, chunk_size: int) -> tuple[QTable, int, dict]:
        opts = dict(dl2=True, simulated=True, observation_info=True)
        with TableLoader(self.file, parent=self, **opts) as load:
            header = self.epp.make_empty_table()
            bits = [header]
            for _, _, events in load.read_subarray_events_chunked(chunk_size, **opts):
                events = self.epp.normalise_column_names(events)
                events = self.make_derived_columns(events)
                events = self.epp.event_selection.calculate_selection(events)
                selected = events[events["selected"]]
                selected = self.epp.keep_nescessary_columns_only(selected)
                bits.append(selected)

            bits.append(header)  # Putting it last ensures the correct metadata is used
            table = vstack(bits, join_type="exact", metadata_conflicts="silent")
            return table

    def get_simulation_information(
        self, obs_time: u.Quantity
    ) -> tuple[SimulatedEventsInfo, PowerLaw]:
        opts = dict(dl2=True, simulated=True, observation_info=True)
        with TableLoader(self.file, parent=self, **opts) as load:
            sim = load.read_simulation_configuration()
            try:
                show = load.read_shower_distribution()
            except NoSuchNodeError:
                # Fall back to using the run header
                show = Table([sim["n_showers"]], names=["n_entries"], dtype=[np.int64])

            for itm in ["spectral_index", "energy_range_min", "energy_range_max"]:
                if len(np.unique(sim[itm])) > 1:
                    raise NotImplementedError(
                        f"Unsupported: '{itm}' differs across simulation runs"
                    )

            sim_info = SimulatedEventsInfo(
                n_showers=show["n_entries"].sum(),
                energy_min=sim["energy_range_min"].quantity[0],
                energy_max=sim["energy_range_max"].quantity[0],
                max_impact=sim["max_scatter_range"].quantity[0],
                spectral_index=sim["spectral_index"][0],
                viewcone_max=sim["max_viewcone_radius"].quantity[0],
                viewcone_min=sim["min_viewcone_radius"].quantity[0],
            )

        meta = {
            "sim_info": sim_info,
            "spectrum": PowerLaw.from_simulation(sim_info, obstime=obs_time),
        }
        return meta

    def make_derived_columns(self, events: QTable) -> QTable:
        events["weight"] = (
            1.0 * u.dimensionless_unscaled
        )  # defer calculation of proper weights to later
        events["gh_score"].unit = u.dimensionless_unscaled
        events["theta"] = calculate_theta(
            events,
            assumed_source_az=events["true_az"],
            assumed_source_alt=events["true_alt"],
        )
        events["true_source_fov_offset"] = calculate_source_fov_offset(
            events, prefix="true"
        )
        events["reco_source_fov_offset"] = calculate_source_fov_offset(
            events, prefix="reco"
        )

        altaz = AltAz()
        pointing = SkyCoord(
            alt=events["pointing_alt"], az=events["pointing_az"], frame=altaz
        )
        reco = SkyCoord(
            alt=events["reco_alt"],
            az=events["reco_az"],
            frame=altaz,
        )
        nominal = NominalFrame(origin=pointing)
        reco_nominal = reco.transform_to(nominal)
        events["reco_fov_lon"] = u.Quantity(-reco_nominal.fov_lon)  # minus for GADF
        events["reco_fov_lat"] = u.Quantity(reco_nominal.fov_lat)
        return events

    def make_event_weights(
        self,
        events: QTable,
        spectrum: PowerLaw,
        kind: str,
        fov_offset_bins: u.Quantity | None = None,
    ) -> QTable:
        if self.target_spectrum is None:
            raise Exception(
                "No spectrum is defined, need a spectrum for events weighting"
            )

        if (
            kind == "gammas"
            and self.target_spectrum.normalization.unit.is_equivalent(
                POINT_SOURCE_FLUX_UNIT
            )
            and spectrum.normalization.unit.is_equivalent(DIFFUSE_FLUX_UNIT)
        ):
            if fov_offset_bins is None:
                raise ValueError(
                    "gamma_target_spectrum is point-like, but no fov offset bins "
                    "for the integration of the simulated diffuse spectrum were given."
                )

            for low, high in zip(fov_offset_bins[:-1], fov_offset_bins[1:]):
                fov_mask = events["true_source_fov_offset"] >= low
                fov_mask &= events["true_source_fov_offset"] < high

                events["weight"][fov_mask] = calculate_event_weights(
                    events[fov_mask]["true_energy"],
                    target_spectrum=self.target_spectrum,
                    simulated_spectrum=spectrum.integrate_cone(low, high),
                )
        else:
            events["weight"] = calculate_event_weights(
                events["true_energy"],
                target_spectrum=self.target_spectrum,
                simulated_spectrum=spectrum,
            )

        return events
