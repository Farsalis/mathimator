"""Formula LaTeX limited to an allowlist of math commands (D18).

A project can come from someone else, and TeX reads files when asked:
``\\input`` would draw any file the user can read into the frame. Every control
word must therefore be a math command below, and ``\\begin`` and ``\\end`` must
name a math environment, since ``\\begin{name}`` runs ``\\name``. ``^^`` is
refused because it spells a character by its code, which would let
``\\inp^^75t`` through a check by name.

The commands come from LaTeX, amsmath and amssymb, the packages of Manim's
default template. A backslash before whitespace or at the end is a control
space in TeX.
"""

from __future__ import annotations

from mathimator_worker.document_error import DocumentError

# Greek and Hebrew letters, symbols, operators, relations, arrows, big operators,
# function names, delimiters, constructions and accents, fonts, text and spacing.
_COMMAND_NAMES = """
    alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa varkappa lambda mu nu xi
    pi varpi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega
    Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
    varGamma varDelta varTheta varLambda varXi varPi varSigma varUpsilon varPhi varPsi varOmega
    aleph beth hbar hslash ell wp Re Im imath jmath infty partial nabla emptyset varnothing
    forall exists nexists neg lnot land lor top bot angle measuredangle triangle square blacksquare
    prime backprime checkmark complement surd flat natural sharp
    pm mp times div cdot cdotp ast star circ bullet oplus ominus otimes oslash odot
    cap cup uplus sqcap sqcup vee wedge setminus smallsetminus dagger ddagger diamond
    leq le geq ge neq ne equiv approx approxeq sim simeq cong propto ll gg leqslant geqslant lesssim gtrsim
    nleq ngeq subset supset subseteq supseteq subsetneq supsetneq in ni notin mid nmid parallel perp
    prec succ preceq succeq doteq asymp models vdash dashv therefore because
    to gets rightarrow leftarrow leftrightarrow Rightarrow Leftarrow Leftrightarrow
    longrightarrow longleftarrow longleftrightarrow Longrightarrow Longleftarrow Longleftrightarrow
    mapsto longmapsto implies impliedby iff uparrow downarrow updownarrow Uparrow Downarrow Updownarrow
    nearrow searrow swarrow nwarrow hookrightarrow hookleftarrow rightleftharpoons xrightarrow xleftarrow
    sum prod coprod int iint iiint oint bigcup bigcap bigoplus bigotimes bigodot bigvee bigwedge bigsqcup
    limits nolimits
    sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh coth log ln lg exp
    lim liminf limsup max min sup inf det deg dim gcd hom ker arg Pr bmod pmod mod
    langle rangle lfloor rfloor lceil rceil lvert rvert lVert rVert vert Vert lbrace rbrace backslash
    left right middle big Big bigg Bigg bigl bigr Bigl Bigr biggl biggr Biggl Biggr
    ldots cdots vdots ddots dots colon
    frac dfrac tfrac cfrac sqrt binom dbinom tbinom overset underset stackrel substack boxed not hline
    hat widehat bar overline underline vec overrightarrow overleftarrow dot ddot tilde widetilde
    acute grave breve check mathring overbrace underbrace
    mathrm mathbf mathit mathsf mathtt mathcal mathbb mathfrak boldsymbol
    text textrm textbf textit textsf texttt textnormal mbox operatorname
    quad qquad phantom hphantom vphantom displaystyle textstyle scriptstyle scriptscriptstyle
"""
COMMANDS = frozenset(_COMMAND_NAMES.split())
ENVIRONMENTS = frozenset(
    {
        "matrix",
        "pmatrix",
        "bmatrix",
        "Bmatrix",
        "vmatrix",
        "Vmatrix",
        "smallmatrix",
        "cases",
        "aligned",
        "gathered",
        "array",
    }
)
# Control symbols: math spacing, a line break, escaped special characters, and
# whitespace, after which a backslash is a control space.
_SYMBOLS = frozenset(",:;! \t\r\n\\{}|%&#$_")


def check_latex(source: str, field: str) -> None:
    """Refuse ``source`` with ``untrusted_latex`` when it uses anything outside the allowlist."""
    if "^^" in source:
        raise _untrusted(field, "^^")
    index = source.find("\\")
    while index != -1:
        name, index = _control_sequence(source, index + 1)
        if name in {"begin", "end"}:
            environment, index = _environment(source, index)
            if environment not in ENVIRONMENTS:
                raise _untrusted(field, f"\\{name}{{{environment}}}" if environment else f"\\{name}")
        elif not (name in COMMANDS or name in _SYMBOLS or name == ""):
            raise _untrusted(field, f"\\{name}")
        index = source.find("\\", index)


def _control_sequence(source: str, start: int) -> tuple[str, int]:
    """The name after a backslash at ``start - 1``: a run of ASCII letters, one other character, or nothing."""
    end = start
    while end < len(source) and source[end].isascii() and source[end].isalpha():
        end += 1
    if end == start and end < len(source):
        end += 1
    return source[start:end], end


def _environment(source: str, start: int) -> tuple[str, int]:
    """The braced name after ``\\begin`` or ``\\end``, or "" when there is none."""
    open_at = start
    while open_at < len(source) and source[open_at] in " \t\r\n":
        open_at += 1
    close_at = source.find("}", open_at)
    if not source.startswith("{", open_at) or close_at == -1:
        return "", start
    return source[open_at + 1 : close_at], close_at + 1


def _untrusted(field: str, command: str) -> DocumentError:
    return DocumentError(f"{field} uses {command}, which formulas cannot use.", code="untrusted_latex", field=field)
