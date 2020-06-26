import json
from fontTools.misc.transform import Identity


class Transformation:
    def __init__(self, data):
        self.data = data
        self.transform = Identity
        if data:
            xOffset = data.get('xOffset', 0)
            yOffset = data.get('yOffset', 0)
            if xOffset or yOffset:
                self.transform = self.transform.translate(xOffset, yOffset)
            xScale = data.get('xScale', 1)
            yScale = data.get('yScale', 1)
            if xScale != 1 and yScale != 1:
                self.transform = self.transform.scale(xScale, yScale)

    def transformPoint(self, x, y):
        return self.transform.transformPoint((x, y))

    def __bool__(self):
        return bool(self.transform)

    def __repr__(self):
        return f'{self.transform.toPS()}'


class Component:
    def __init__(self, data):
        self.data = data
        self.name = data.get('component').get('glyphName')
        self.transform = Transformation(data.get('transform'))

    def __repr__(self):
        if self.transform:
            return f'<{self.__class__.__name__} "{self.name}" {self.transform}>'
        return f'<{self.__class__.__name__} "{self.name}">'


class Anchor:
    def __init__(self, data):
        self.data = data
        self.name = data.get('name')
        self.x, self.y = [float(v) for v in data.get('point').split()]

    def __repr__(self):
        name, x, y = self.name, self.x, self.y
        return f'<{self.__class__.__name__} "{name}" x={x:g} y={y:g}>'


class Anchors:
    def __init__(self, data):
        self.data = data
        self.anchors = {a.get('name'): Anchor(a) for a in data}

    def addAnchor(self, data):
        self[data['name']] = Anchor(data)
        self.data.append(data)

    def __len__(self):
        return len(self.anchors)

    def __contains__(self, name):
        return name in self.anchors

    def __getitem__(self, name):
        return self.anchors.get(name)

    def __setitem__(self, name, anchor):
        self.anchors[name] = anchor

    def __iter__(self):
        for name in self.anchors:
            yield self[name]

    def __repr__(self):
        return repr(list(self.anchors.values()))


class Layer:
    def __init__(self, data, glyph=None):
        self.data = data
        self.glyph = glyph
        self.name = data.get('name')
        self.anchors = Anchors(data.get('anchors', []))

        elements = data.get('elements', [])
        self.components = [Component(e) for e in elements if e.get('component')]
        self._anchorsPropagated = False

    def _addAnchors(self, name):
        font = self.glyph.font

        # Collect anchors from components.
        anchors = []
        for component in self.components:
            layer = font[component.name].layers[self.name]
            for anchor in layer.anchors:
                if anchor.name == name:
                    x, y = component.transform.transformPoint(anchor.x, anchor.y)
                    anchors.append((x, y))
                    break

        # For multiple mkmk anchors, keep only the top most or bottom most one.
        if name.endswith('.mkmk'):
            anchors = [max(anchors, key=lambda x: abs(x[1]))]

        for i, (x, y) in enumerate(anchors):
            n = name
            if len(anchors) > 1:
                # Multiple anchors, turn into ligature anchor.
                n = f'{name}_{i + 1}'
            self.anchors.addAnchor(dict(name=n, point=f'{x:g} {y:g}'))

    def propagateAnchors(self):
        if self._anchorsPropagated:
            return

        self._anchorsPropagated = True
        font = self.glyph.font

        # Collect anchor names.
        names = set()
        for component in self.components:
            layer = font[component.name].layers[self.name]
            layer.propagateAnchors()
            names |= {a.name for a in layer.anchors}

        # Add anchors.
        for name in sorted(names):
            # Skip mark anchors, or base anchors with corresponding mkmk ones.
            if name.startswith('_') or f'{name}.mkmk' in names:
                continue
            if not any(a.name.startswith(name) for a in self.anchors):
                self._addAnchors(name)

        if self.anchors and 'anchors' not in self.data:
            self.data['anchors'] = self.anchors.data

    def __repr__(self):
        return f'<{self.__class__.__name__} "{self.name}">'


class Layers:
    def __init__(self, data, glyph=None):
        self.data = data
        self.layers = {l.get('name'): Layer(l, glyph) for l in data}

    def __len__(self):
        return len(self.layers)

    def __contains__(self, name):
        return name in self.layers

    def __getitem__(self, name):
        return self.layers.get(name)

    def __iter__(self):
        for name in self.layers:
            yield self[name]

    def __repr__(self):
        return repr(list(self.layers.values()))


class Glyph:
    def __init__(self, data, font=None):
        self.data = data
        self.font = font
        self.name = data.get('name')
        self.openTypeGlyphClass = data.get('openTypeGlyphClass')
        self.layers = Layers(data.get('layers', []), self)
        self.unicode = data.get('unicode')
        if self.unicode:
            self.unicode = [int(u, 16) for u in self.unicode.split(',')]

    def propagateAnchors(self):
        for layer in self.layers:
            layer.propagateAnchors()

    def __repr__(self):
        return f'<{self.__class__.__name__} "{self.name}">'


class Master:
    def __init__(self, data, font=None):
        self.font = font
        self.data = data['fontMaster']
        self.name = self.data.get('name')
        self.psn = self.data.get('psn')

    def __repr__(self):
        return f'<{self.__class__.__name__} "{self.name}">'


class Info:
    def __init__(self, data):
        self.data = data
        self.tfn = data.get('tfn')

    def __repr__(self):
        return f'<{self.__class__.__name__} "{self.tfn}">'


class Font:
    def __init__(self, path):
        with open(path) as f:
            data = json.load(f)
        self.data = data
        self.version = data.get('version')
        assert self.version == 8, f'Unsupported VFJ version: {self.version}'

        data = data.get('font')

        self.glyphs = {g.get('name'): Glyph(g, self) for g in data.get('glyphs')}
        assert len(self.glyphs) == data.get('glyphsCount')

        self.masters = [Master(m, self) for m in data.get('masters', [])]

        self.info = Info(data.get('info'))

    def propagateAnchors(self):
        for glyph in self:
            glyph.propagateAnchors()

    def save(self, path):
        with open(path, 'w') as f:
            json.dump(self.data, f, indent=2)

    def __len__(self):
        return len(self.glyphs)

    def __contains__(self, name):
        return name in self.glyphs

    def __getitem__(self, name):
        return self.glyphs.get(name)

    def __iter__(self):
        for name in self.glyphs:
            yield self[name]

    def __repr__(self):
        return f'<{self.__class__.__name__} "{self.info.tfn}">'