# Release-tree policy

This package owns checks that interpret a Git candidate tree in hosted CI. They
remain separate from `dev.py` and public Loom tooling because release admission
depends on repository state, not on the validity of an individual library or
compiler input.

Feature branches may track and exercise packages below `experimental/`. A
candidate for the repository's release line must instead contain only admitted
library paths. The check reads the candidate index rather than branch names or
history, allowing an incubation branch to promote reusable work and delete its
experimental payload before review.
