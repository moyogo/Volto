import re

from tempfile import NamedTemporaryFile

from fontTools.ttLib import TTFont
from fontTools.feaLib import ast
from fontTools.voltLib import ast as VoltAst
from fontTools.voltLib.parser import Parser as VoltParser


class VtpToFea:
    _NAME_START_RE = re.compile(r"[A-Za-z_+*:.\^~!\\]")
    _NOT_NAME_RE = re.compile(r"[^A-Za-z0-9_.+*:\^~!/-]")
    _NOT_CLASS_NAME_RE = re.compile(r"[^A-Za-z_0-9.]")

    def __init__(self, filename):
        self._filename = filename

        self._glyph_map = {}
        self._glyph_order = None

        self._doc = ast.FeatureFile()
        self._gdef = {}
        self._groups = {}
        self._features = {}
        self._lookups = {}

    def _lookupName(self, name):
        if self._NAME_START_RE.match(name[0]) is None:
            name = "_" + name
        out = self._NOT_NAME_RE.sub("_", name)
        return out

    def _className(self, name):
        return self._NOT_CLASS_NAME_RE.sub("_", name)

    def _parse(self, filename):
        font = None
        try:
            font = TTFont(filename)
            if "TSIV" in font:
                with NamedTemporaryFile(delete=False) as temp:
                    temp.write(font["TSIV"].data)
                    temp.flush()
                    parser = VoltParser(temp.name)
        except:
            parser = VoltParser(filename)

        return parser.parse(), font

    def convert(self, path):
        volt_doc, font = self._parse(self._filename)

        if font is not None:
            self._glyph_order = font.getGlyphOrder()

        reported = []
        for statement in volt_doc.statements:
            name = type(statement).__name__
            if hasattr(self, name):
                getattr(self, name)(statement)
            elif name not in reported:
                print("Can’t handle: %s" % name)
                reported.append(name)

        statements = self._doc.statements

        for ftag, scripts in self._features.items():
            feature = ast.FeatureBlock(ftag)
            for stag, langs in scripts.items():
                script = ast.ScriptStatement(stag)
                feature.statements.append(script)
                for ltag, lookups in langs.items():
                    lang = ast.LanguageStatement(ltag)
                    feature.statements.append(lang)
                    for name in lookups:
                        lookup = self._lookups[name]
                        lookupref = ast.LookupReferenceStatement(lookup)
                        feature.statements.append(lookupref)
            statements.append(feature)

        gdef = ast.TableBlock("GDEF")
        gdef.statements.append(
            ast.GlyphClassDefStatement(self._gdef.get("BASE"),
                                       self._gdef.get("MARK"),
                                       self._gdef.get("LIGATURE"),
                                       self._gdef.get("COMPONENT")))

        statements.append(gdef)

        with open(path, "w") as feafile:
            feafile.write(self._doc.asFea())

    def GlyphName(self, glyph):
        try:
            name = glyph.glyph
        except AttributeError:
            name = glyph
        return ast.GlyphName(self._glyph_map[name])

    def GroupName(self, group):
        try:
            name = group.group
        except AttributeError:
            name = group
        return ast.GlyphClassName(self._groups[name])

    def Coverage(self, coverage):
        items = []
        for item in coverage:
            if isinstance(item, VoltAst.GlyphName):
                items.append(self.GlyphName(item))
            elif isinstance(item, VoltAst.GroupName):
                items.append(self.GroupName(item))
            elif isinstance(item, VoltAst.Enum):
                items.append(self.Enum(item))
            else:
                assert False, "%s is not handled" % item
        return items

    def Enum(self, enum):
        return ast.GlyphClass(self.Coverage(enum.enum))

    def Context(self, context):
        coverage = self.Coverage(context)
        if not isinstance(coverage, (tuple, list)):
            coverage = [coverage]
        return coverage

    def GroupDefinition(self, group):
        name = self._className(group.name)
        glyphs = self.Enum(group.enum)
        glyphclass = ast.GlyphClassDefinition(name, glyphs)
        self._groups[group.name] = glyphclass
        self._doc.statements.append(glyphclass)

    def GlyphDefinition(self, glyph):
        try:
            self._glyph_map[glyph.name] = self._glyph_order[glyph.id]
        except TypeError:
            self._glyph_map[glyph.name] = glyph.name

        if glyph.type not in self._gdef:
            self._gdef[glyph.type] = ast.GlyphClass()
        self._gdef[glyph.type].glyphs.append(glyph.name)

    def ScriptDefinition(self, script):
        for lang in script.langs:
            for feature in lang.features:
                if feature.tag not in self._features:
                    self._features[feature.tag] = {}
                if script.tag not in self._features[feature.tag]:
                    self._features[feature.tag][script.tag] = {}
                assert lang.tag not in self._features[feature.tag][script.tag]
                self._features[feature.tag][script.tag][lang.tag] = feature.lookups

    def LookupDefinition(self, lookup):
        mark_attachement = None
        mark_filtering = None

        flags = 0
        if lookup.direction == "RTL":
            flags |= 1
        if not lookup.process_base:
            flags |= 2
        # FIXME: Does VOLT support this?
        #if not lookup.process_ligatures:
        #    flags |= 4
        if not lookup.process_marks:
            flags |= 8
        elif isinstance(lookup.process_marks, str):
            name = lookup.process_marks
            mark_attachement = ast.GlyphClassName(self._groups[name])
        elif lookup.mark_glyph_set is not None:
            name = lookup.mark_glyph_set
            mark_filtering = ast.GlyphClassName(self._groups[name])

        fea_lookup = ast.LookupBlock(self._lookupName(lookup.name))
        if flags or mark_attachement is not None or mark_filtering is not None:
            lookupflags = ast.LookupFlagStatement(flags, mark_attachement,
                                                  mark_filtering)
            fea_lookup.statements.append(lookupflags)

        if lookup.sub is not None:
            sub = lookup.sub

            prefix = []
            suffix = []
            if lookup.context:
                context = lookup.context[0]
                if context.left:
                    assert(len(context.left) == 1) # FIXME
                    prefix = self.Context(context.left[0])
                if context.right:
                    assert(len(context.right) == 1) # FIXME
                    suffix = self.Context(context.right[0])

            for key, val in sub.mapping.items():
                subst = None
                glyphs = self.Coverage(key)
                replacement = self.Coverage(val)
                if isinstance(sub, VoltAst.SubstitutionSingleDefinition):
                    assert(len(glyphs) == 1)
                    assert(len(replacement) == 1)
                    subst = ast.SingleSubstStatement(glyphs, replacement,
                                prefix, suffix, False)
                elif isinstance(sub, VoltAst.SubstitutionMultipleDefinition):
                    assert(len(glyphs) == 1)
                    subst = ast.MultipleSubstStatement(prefix, glyphs[0], suffix,
                                replacement)
                elif isinstance(sub, VoltAst.SubstitutionLigatureDefinition):
                    assert(len(replacement) == 1)
                    subst = ast.LigatureSubstStatement(prefix, glyphs,
                                suffix, replacement[0], False)
                else:
                    assert False, "%s is not handled" % sub
                fea_lookup.statements.append(subst)

        if lookup.pos is not None:
            pass

        self._lookups[lookup.name] = fea_lookup
        if lookup.comments is not None:
            self._doc.statements.append(ast.Comment(lookup.comments))
        self._doc.statements.append(fea_lookup)


def main(filename, outfilename):
    converter = VtpToFea(filename)
    converter.convert(outfilename)

if __name__ == '__main__':
    import sys
    if len(sys.argv) == 3:
        main(sys.argv[1], sys.argv[2])
    else:
        print('Usage: %s voltfile feafile' % sys.argv[0])
        sys.exit(1)
