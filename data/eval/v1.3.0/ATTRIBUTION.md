# Attribution

This dataset contains modified BPMN models and copied process descriptions from
the PMo Dataset v1.0.0 by Alexis Brissard, Frédéric Cuppens, and Amal Zouaq:
https://doi.org/10.5281/zenodo.15857589.

The source dataset is licensed under Creative Commons Attribution 4.0
(CC BY 4.0). The files in `variants/` modify the source BPMN models by applying
the defect operators recorded in the matching `ground_truth/` files. The files
in `seeds/` and `descriptions/seeds/` are unmodified copies. Variant descriptions
are unmodified copies associated with the corresponding source model.

Generator code, operator identifiers, file hashes, and generation parameters are
recorded in `manifest.json` and `evaluation/generator/`.
