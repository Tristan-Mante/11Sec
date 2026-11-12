# -*- coding: utf-8 -*-
"""
Straight UVs Maya - redressage de UV en un clic
------------------------------------------------
Usage (Script Editor, onglet Python) :

    import straight_uvs_maya
    straight_uvs_maya.show()

Selection acceptee : objets, faces ou UV / shells UV (plusieurs meshes possibles).
Fonctionne surtout sur des shells a topologie quad (bandes, grilles, cylindres decoupes...).
"""

import math
import maya.cmds as cmds
import maya.mel as mel

WINDOW = "straightUVsMayaWin"
VERSION = "1.0"


# ----------------------------------------------------------------------------
# Utilitaires
# ----------------------------------------------------------------------------
def _flat(x):
    return cmds.ls(x, flatten=True) or []


def _get_shells(selection):
    """Retourne une liste de shells (liste de noms de UV) a partir de la selection."""
    sel_uvs = set(_flat(cmds.polyListComponentConversion(selection, toUV=True)))
    meshes = sorted({u.split(".")[0] for u in sel_uvs})
    shells = []

    for mesh in meshes:
        # Union-find : tous les UV d'une meme face appartiennent au meme shell
        parent = {}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        nfaces = cmds.polyEvaluate(mesh, face=True)
        for i in range(nfaces):
            fuvs = _flat(cmds.polyListComponentConversion("%s.f[%d]" % (mesh, i), toUV=True))
            for uv in fuvs:
                parent.setdefault(uv, uv)
            for uv in fuvs[1:]:
                union(fuvs[0], uv)

        groups = {}
        for uv in parent:
            groups.setdefault(find(uv), []).append(uv)

        # On ne garde que les shells touchant la selection
        for members in groups.values():
            if any(u in sel_uvs for u in members):
                shells.append(sorted(members))

    return shells


def _shell_faces(shell):
    faces = _flat(cmds.polyListComponentConversion(shell, toFace=True, internal=True))
    if not faces:
        faces = _flat(cmds.polyListComponentConversion(shell, toFace=True))
    return faces


def _shell_positions(shell):
    vals = cmds.polyEditUV(shell, q=True)
    return {shell[i]: (vals[2 * i], vals[2 * i + 1]) for i in range(len(shell))}


def _bbox(pos):
    us = [p[0] for p in pos.values()]
    vs = [p[1] for p in pos.values()]
    return min(us), min(vs), max(us), max(vs)


def _face_corners(face):
    """Pour une face quad : liste ordonnee de (nom_uv, position_3D). None sinon."""
    node = face.split(".")[0]
    fid = int(face.split("[")[-1].rstrip("]"))
    info = cmds.polyInfo(face, faceToVertex=True)
    if not info:
        return None
    verts = [int(v) for v in info[0].split(":")[1].split()]
    if len(verts) != 4:
        return None
    corners = []
    for v in verts:
        vf = "%s.vtxFace[%d][%d]" % (node, v, fid)
        uvs = _flat(cmds.polyListComponentConversion(vf, toUV=True))
        if not uvs:
            return None
        p3 = cmds.pointPosition("%s.vtx[%d]" % (node, v), world=True)
        corners.append((uvs[0], p3))
    return corners


def _edges(face_data, pos):
    """Genere (du, dv, longueur3D) pour chaque arete de chaque quad."""
    for corners in face_data:
        for i in range(4):
            a, b = corners[i], corners[(i + 1) % 4]
            ua, va = pos[a[0]]
            ub, vb = pos[b[0]]
            p, q = a[1], b[1]
            l3 = math.sqrt(sum((p[k] - q[k]) ** 2 for k in range(3)))
            yield ub - ua, vb - va, l3


def _grid_angle(face_data, pos):
    """Angle (rad) de deviation de la grille UV par rapport aux axes U/V."""
    c = s = 0.0
    for du, dv, l3 in _edges(face_data, pos):
        l = math.hypot(du, dv)
        if l < 1e-12:
            continue
        t = math.atan2(dv, du)
        c += l * math.cos(4 * t)
        s += l * math.sin(4 * t)
    if abs(c) < 1e-12 and abs(s) < 1e-12:
        return 0.0
    return math.atan2(s, c) / 4.0


def _rotate(shell, angle_deg, pivot):
    cmds.polyEditUV(shell, pivotU=pivot[0], pivotV=pivot[1], angle=angle_deg)


def _scale(shell, su, sv, pivot):
    cmds.polyEditUV(shell, pivotU=pivot[0], pivotV=pivot[1], scaleU=su, scaleV=sv)


def _center(pos):
    u0, v0, u1, v1 = _bbox(pos)
    return ((u0 + u1) * 0.5, (v0 + v1) * 0.5)


# ----------------------------------------------------------------------------
# Traitement d'un shell
# ----------------------------------------------------------------------------
def _straighten_shell(shell, horizontal=True, use_3d=True):
    # 1) Redressage natif de Maya (aligne les boucles d'aretes en lignes droites)
    cmds.select(shell, replace=True)
    try:
        cmds.texStraightenShell()
    except Exception:
        mel.eval("texStraightenShell")

    pos = _shell_positions(shell)

    faces = _shell_faces(shell)
    face_data = [fd for fd in (_face_corners(f) for f in faces) if fd]

    # 2) Alignement sur les axes U/V
    if face_data:
        ang = _grid_angle(face_data, pos)
        if abs(ang) > 1e-6:
            deg = math.degrees(ang)
            _rotate(shell, -deg, _center(pos))
            new_pos = _shell_positions(shell)
            # Le sens de rotation est verifie : si ca empire, on inverse
            if abs(_grid_angle(face_data, new_pos)) > abs(ang) * 0.5:
                _rotate(shell, 2 * deg, _center(new_pos))
            pos = _shell_positions(shell)

    # 3) Proportions basees sur les longueurs d'aretes 3D (densite de texel U = V)
    if use_3d and face_data:
        h3 = hu = v3 = vu = 0.0
        for du, dv, l3 in _edges(face_data, pos):
            if abs(du) >= abs(dv):
                h3 += l3
                hu += abs(du)
            else:
                v3 += l3
                vu += abs(dv)
        if hu > 1e-9 and vu > 1e-9 and h3 > 1e-9 and v3 > 1e-9:
            k = (h3 / hu) / (v3 / vu)  # rapport de densite U / V
            su, sv = math.sqrt(k), 1.0 / math.sqrt(k)
            _scale(shell, su, sv, _center(pos))
            pos = _shell_positions(shell)

    # 4) Orientation horizontale / verticale
    u0, v0, u1, v1 = _bbox(pos)
    w, h = u1 - u0, v1 - v0
    if (horizontal and h > w) or ((not horizontal) and w > h):
        _rotate(shell, 90.0, _center(pos))


# ----------------------------------------------------------------------------
# Commande principale
# ----------------------------------------------------------------------------
def straighten_selection(horizontal=True, use_3d=True, layout=False):
    sel = cmds.ls(selection=True)
    if not sel:
        cmds.warning("Straight UVs : selectionne un mesh, des faces ou des UV.")
        return 0

    shells = _get_shells(sel)
    if not shells:
        cmds.warning("Straight UVs : aucun shell UV trouve dans la selection.")
        return 0

    cmds.undoInfo(openChunk=True, chunkName="StraightUVs")
    done = 0
    all_faces = []
    try:
        cmds.progressWindow(title="Straight UVs", progress=0, maxValue=len(shells), isInterruptable=True)
        for i, shell in enumerate(shells):
            if cmds.progressWindow(q=True, isCancelled=True):
                break
            try:
                _straighten_shell(shell, horizontal, use_3d)
                all_faces.extend(_shell_faces(shell))
                done += 1
            except Exception as e:
                cmds.warning("Shell ignore (%s)" % e)
            cmds.progressWindow(e=True, progress=i + 1)
        if layout and all_faces:
            cmds.polyLayoutUV(all_faces, layout=2, layoutMethod=1, percentageSpace=0.2,
                              rotateForBestFit=0, scale=1, separate=0, flipReversed=False)
    finally:
        cmds.progressWindow(endProgress=True)
        cmds.undoInfo(closeChunk=True)
        try:
            cmds.select(sel, replace=True)
        except Exception:
            pass
    return done


# ----------------------------------------------------------------------------
# Interface
# ----------------------------------------------------------------------------
def _on_straight(*_):
    horizontal = cmds.radioButtonGrp("suvDir", q=True, select=True) == 1
    use_3d = cmds.checkBox("suvUse3d", q=True, value=True)
    layout = cmds.checkBox("suvLayout", q=True, value=True)
    cmds.text("suvStatus", e=True, label="Traitement...")
    n = straighten_selection(horizontal, use_3d, layout)
    cmds.text("suvStatus", e=True, label="Termine : %d shell(s)" % n if n else "Pret")


def _on_about(*_):
    cmds.confirmDialog(
        title="A propos",
        message="Straight UVs Maya v%s\nRedressage de UV en un clic pour Autodesk Maya." % VERSION,
        button=["OK"])


def show():
    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW)
    cmds.window(WINDOW, title="Straight UVs Maya", widthHeight=(260, 230), sizeable=False)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=6, columnOffset=("both", 10))
    cmds.separator(height=6, style="none")
    cmds.text(label="Selectionne mesh / faces / shells UV", align="center")
    cmds.separator(height=4)
    cmds.button(label="STRAIGHT UVs", height=42, backgroundColor=(0.1, 0.6, 0.85), command=_on_straight)
    cmds.radioButtonGrp("suvDir", numberOfRadioButtons=2, labelArray2=("Horizontal", "Vertical"),
                        select=1, columnWidth2=(110, 110))
    cmds.checkBox("suvUse3d", label="Utiliser les longueurs d'aretes 3D (proportions)", value=True)
    cmds.checkBox("suvLayout", label="Ranger les shells dans 0-1 apres", value=False)
    cmds.text("suvStatus", label="Pret", align="center")
    cmds.rowLayout(numberOfColumns=2, columnWidth2=(115, 115), adjustableColumn=1)
    cmds.button(label="About", command=_on_about)
    cmds.button(label="Close", command=lambda *_: cmds.deleteUI(WINDOW))
    cmds.setParent("..")
    cmds.showWindow(WINDOW)


if __name__ == "__main__":
    show()
