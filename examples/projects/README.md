# Srm-Open example projects
#
# Each sub-directory is a self-contained design case:
#   project.yaml    the design inputs (requirement + grain + propellant)
#   synthesis.yaml  requirement-only inputs (forward synthesis)
#   README.md       what the case demonstrates and the expected outcome
#   output/         report.md / report.html / result.json / *.png (regenerated)
#
# Regenerate any case's results with:
#
#   PYTHONPATH=src python examples/run_case.py examples/projects/<case>
#
# These are *teaching* cases using placeholder numbers from the built-in
# databases. They are not validated motor designs.
#
# Cases
# -----
# 01_tube_sounding           fixed geometry, tube       (margin assessment)
# 02_star_neutral            fixed geometry, star       (near-neutral)
# 03_wagon_wheel_regressive  fixed geometry, wagon wheel (regressive)
# 04_multi_perf              fixed geometry, 7-perforation
# 05_two_segment_booster     fixed geometry, tube + star (multi-segment)
# 06_synthesis_star          requirement-only -> synthesised star grain
# 07_synthesis_tube          requirement-only -> synthesised tube grain

# 08_inverse_identifiability   path A (outline->params) + sweep + Monte-Carlo
#                            run: PYTHONPATH=src python \
#                                 examples/projects/08_inverse_identifiability/study.py
