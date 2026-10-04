# UI contrast constraint

## Teal primary fails small-text contrast

**Id:** d0f8140f-8e7d-4200-af91-c9c72ab68fd8
**Type:** constraint
**Status:** active
**Evidence:** confirmed
**Source:** ui-context.md, 2026-10-03; progress-tracker.md S05, 2026-10-04
**Revisit when:** the palette or theme tokens change

The canonical teal `#0A9E8F` against white is approximately 3.33:1 —
insufficient for ordinary small text, including white button labels —
and boldness alone does not fix small text. S05 therefore implemented
a darker action fill: light theme `--primary-action` `#06786D` with
white label (5.36:1; hover `#065F57` at 7.55:1), dark theme
`#2DD4BF` with `#04211d` label (9.09:1). The canonical palette is a
visual reference, not a completed accessibility verification.

**Reason:** the contrast constraint is stated in the UI context and
was verified with computed contrast assertions in both browsers during
S05; readable labels were a functional requirement of the primary
action token.

**Rejected alternative:** keep `#0A9E8F` for primary buttons. Rejected
because it fails contrast for ordinary small text; the darker
`--primary-action` tokens keep the teal identity while meeting the
constraint.
