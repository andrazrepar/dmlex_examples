from lxml import etree
import copy, re, logging, traceback, zipfile, json, pathlib, eventlet, sys,datetime, os, os.path, io, time, codecs, re
from eventlet import wsgi
urllib_parse = eventlet.import_patched("urllib.parse")
urllib_request = eventlet.import_patched("urllib.request")
urllib_error = eventlet.import_patched("urllib.error")

logging.basicConfig(format = "[%(asctime)s] [%(levelname)s] %(message)s",
level = logging.DEBUG)

NS_META = "http://elex.is/wp1/teiLex0Mapper/meta"
ATTR_INNER_TEXT = "{%s}innerText" % NS_META
ATTR_INNER_TEXT_REC = "{%s}innerTextRec" % NS_META
ATTR_CONSTANT = "{%s}constant" % NS_META
ATTR_AUTO = "{%s}autogenerated" % NS_META
CommentType = type(etree.Comment(""))
ElementType = type(etree.Element("x"))

class TMarkerDesc:
    __slots__ = ["inSelector", "namespaces", "outElement", "jsonPlural",
"regex", "compiledRegex", "regexGroup"]
    def __init__(self, jsonDesc):
        self.inSelector = jsonDesc.get("inSelector", None)
        self.outElement = jsonDesc["outElement"]
        self.jsonPlural = jsonDesc.get("jsonPlural", self.outElement + "s")
        self.regex = jsonDesc.get("regex", None)
        self.compiledRegex = None if self.regex is None else re.compile(self.regex)
        self.regexGroup = jsonDesc.get("regexGroup", None)
        self.namespaces = jsonDesc.get("namespaces", None)

class TTextValDesc:
    __slots__ = ["inSelector", "namespaces", "attribute", "constant",
"regex", "compiledRegex", "regexGroup", "outElement", "markers", "xlat"]
    def __init__(self, jsonDesc):
        self.inSelector = jsonDesc.get("inSelector", None) # should be an xpath string; if omitted, we will use the XML element associated with the TEltDesc to which this TTextValDesc belongs
        self.namespaces = jsonDesc.get("namespaces", None)
        self.attribute = jsonDesc["attribute"]
        self.constant = jsonDesc.get("constant", "")
        self.regex = jsonDesc.get("regex", None)
        self.compiledRegex = None if self.regex is None else re.compile(self.regex)
        self.regexGroup = jsonDesc.get("regexGroup", None)
        self.outElement = jsonDesc["outElement"]
        self.xlat = jsonDesc.get("xlat", None)
        markersDesc = jsonDesc.get("markers", None)
        self.markers = None if markersDesc is None else [TMarkerDesc(markerDesc) for markerDesc in markersDesc]

class TEltDesc:
    __slots__ = ["inSelector", "outElement", "jsonPlural", "children", "textVals", "namespaces", "copyToOutElt"]
    def __init__(self, jsonDesc):
        if jsonDesc is None: return
        self.inSelector = jsonDesc["inSelector"] # should be an xpath string
        self.outElement = jsonDesc["outElement"] # should be a string
        self.jsonPlural = jsonDesc.get("jsonPlural", self.outElement + "s")
        self.namespaces = jsonDesc.get("namespaces", None) # should be a dict that maps strings to strings; namespace prefixes used in 'inSelector'
        childDescs = jsonDesc.get("children", None)
        self.children = None if childDescs is None else [TEltDesc(childDesc) for childDesc in childDescs]
        textDescs = jsonDesc.get("textVals", None)
        self.textVals = None if textDescs is None else [TTextValDesc(textDesc) for textDesc in textDescs]
        self.copyToOutElt = jsonDesc.get("copyToOutElt", None)

def BuildKeepAliveHash(xmlTree):
    h = {}
    def Rec(elt):
        if type(elt) is not ElementType: return
        h[id(elt)] = elt
        for child in elt: Rec(child)
    Rec(xmlTree.getroot())
    return h

# Returns a string consisting of the inner text of 'elt'; if 'recurse' ==
True,
# the contents of its descendants are also included recursively.
# For any descendant 'e' of 'elt', the values 'startPos[id(e)]' and 'endPos[id(e)]' receive the
# positions in the resulting string where the contents of 'e' began or ended, respectively.
def BuildInnerText(elt, startPos, endPos, recurse):
    outList = []; curPos = 0
    def Rec(elt):
        nonlocal outList, curPos
        if elt.text: outList.append(elt.text); curPos += len(elt.text)
        for child in elt:
            if type(child) is not ElementType: continue
            startPos[id(child)] = curPos
            if recurse: Rec(child)
            endPos[id(child)] = curPos
            if child.tail: outList.append(child.tail); curPos += len(child.tail)
    Rec(elt)
    return "".join(outList)

def TransformSingleXmlDoc(xmlTree, mainEltDesc):
    """
    Applies the mapping described by 'mainEltDesc' (an instance of TEltDesc)
    to the XML document 'xmlTree' (an instance of etree.ElementTree),
    and returns a list of JSON-like objects (i.e. dicts).
    """
    autogenCounters = {}
    def ProcessTextDesc(xmlElt, outElt, eltDesc, textDesc):
        if textDesc.inSelector is None: xmlElts = [xmlElt]
        else:
            L = xmlElt.xpath(textDesc.inSelector, namespaces = textDesc.namespaces)
            if not L: return
            xmlElt = L[0]
        # Prepare the value of the attribute from which this text item is to be taken.
        eltStartPos = {}; eltEndPos = {}
        if textDesc.attribute == ATTR_CONSTANT:
            attrValue = textDesc.constant
        elif textDesc.attribute == ATTR_AUTO:
            key = eltDesc.outElement + "." + textDesc.outElement
            seqNo = autogenCounters.get(key, 0) + 1
            autogenCounters[key] = seqNo
            attrValue = "%s.%d" % (key, seqNo)
        elif textDesc.attribute == ATTR_INNER_TEXT or textDesc.attribute == ATTR_INNER_TEXT_REC:
            attrValue = BuildInnerText(xmlElt, eltStartPos, eltEndPos, textDesc.attribute == ATTR_INNER_TEXT_REC)
        else:
            attrValue = xmlElt.attrib.get(textDesc.attribute, None)
            if attrValue is None: return
        # Match 'attrValue' against the regex if needed.
        if textDesc.compiledRegex is None:
            matchStart = 0; matchEnd = len(attrValue)
        else:
            m = textDesc.compiledRegex.search(attrValue)
            if not m: return
            grp = 0 if textDesc.regexGroup is None else textDesc.regexGroup
            try:  matchStart = m.start(grp); matchEnd = m.end(grp)
            except: pass # must be an error in the mapping, perhaps an invalid group name
            if matchStart < 0 or matchEnd < 0: return # the group did not contribute to the match
        # Store the matched value in the output object.
        matchLen = matchEnd - matchStart; outValue = attrValue[matchStart:matchEnd]
        outValueXlat = outValue if not textDesc.xlat else textDesc.xlat.get(outValue, outValue)
        outElt[textDesc.outElement] = outValueXlat
        # Process markers, if needed.
        if textDesc.markers:
            for markerDesc in textDesc.markers:
                markerEltSubranges = []
                # If an xpath expression is present, it defines a set of elements and each of these elements
                # represents a substring within our 'attrValue'.  Otherwise we have a single range
                # representing the whole 'attrValue'.
                if not markerDesc.inSelector: markerEltSubranges = [(0, matchLen)]
                else:
                    for markerElt in xmlElt.xpath(markerDesc.inselector, namespaces = markerDesc.namespaces):
                        idxFrom = eltStartPos.get(id(markerElt), -1); idxTo = eltEndPos.get(id(markerElt), -1)
                        # If this element does not appear in 'eltStartPos' and 'eltEndPos', perhaps 'attrVal'
                        # was really taken from an attribute rather than from INNER_TEXT_REC.  In this case
                        # the user should not have provided an xpath expression in the marker description.
                        if idxFrom < 0 or idxTo < 0: continue
                        # Perhaps the element does not overlap with the matched part of 'attrVal';
                        # in this case it will not generate any markers.
                        if idxTo <= matchEnd or idxFrom >= matchStart: continue
                        idxFrom = max(idxFrom - matchStart, 0); idxTo = max(idxTo - matchStart, 0)
                        markerEltSubranges.append((idxFrom, idxTo))
                # Test each range against the regex, if one was provided.
                # We'll search for occurrences of the regex in the string, rather than trying to match the
                # whole string against the regex, so that each string can generate several pairs of markers
                # if there are several matches.
                if markerDesc.compiledRegex is not None:
                    rangesToSearch = markerEltSubranges; markerEltSubranges = []
                    for (idxFrom, idxTo) in rangesToSearch:
                        strToSearch = outValue[idxFrom:idxTo]
                        for m2 in markerDesc.compiledRegex.finditer(strToSearch):
                            grp2 = 0 if markerDesc.regexGroup is None else markerDesc.regexGroup
                            try: matchStart2 = m2.start(grp2); matchEnd2 = m2.end(grp2)
                            except: continue
                            if matchStart2 < 0 or matchEnd2 < 0: continue
                            markerEltSubranges.append((idxFrom + matchStart2, idxFrom + matchEnd2))
                # Remove any duplicates, just in case.
                markerEltSubranges = list(set(markerEltSubranges)); markerEltSubranges.sort()
                # Construct suitable objects and store them under 'outElt'.
                outList = outElt.get(markerDesc.jsonPlural, [])
                for (idxFrom, idxTo) in markerEltSubranges:
                    outList.append({"startIndex": idxFrom, "endIndex": idxTo})
                outElt[markerDesc.jsonPlural] = outList
    def ProcessEltDesc(xmlParentElt, eltDesc):
        outListJson = []
        for xmlElt in (xmlTree if xmlParentElt is None else xmlParentElt).xpath(eltDesc.inSelector, namespaces = eltDesc.namespaces):
            outJsonElt = {}
            if eltDesc.children:
                for childDesc in eltDesc.children:
                    childJsonList = ProcessEltDesc(xmlElt, childDesc)
                    if childJsonList:
                        if childDesc.jsonPlural in outJsonElt: outJsonElt[childDesc.jsonPlural] += childJsonList
                        else: outJsonElt[childDesc.jsonPlural] = childJsonList
            if eltDesc.textVals:
                for textDesc in eltDesc.textVals:
                    ProcessTextDesc(xmlElt, outJsonElt, eltDesc, textDesc)
            if eltDesc.copyToOutElt: outJsonElt.update(eltDesc.copyToOutElt)
            outListJson.append(outJsonElt)
        return outListJson
    keepAliveHash = BuildKeepAliveHash(xmlTree)
    resultsJson = ProcessEltDesc(None, mainEltDesc)
    return resultsJson

def ExtractEntries(xmlTree, mainEltDesc, targetOutElement = "entry"):
    """
    Applies the mapping described by 'mainEltDesc' (an instance of TEltDesc)
    to the XML document 'xmlTree' (an instance of etree.ElementTree).
    For every XML element to which an element mapping description with outElement = targetOutElement
    would be applied, we prepare a string serialization of this XML element
    and return a list of such serializations.  This is intended to be used for
    entries but could in principle be used for any out-element.
    """
    outList = []
    def ProcessEltDesc(xmlParentElt, eltDesc):
        nonlocal outList
        for xmlElt in (xmlTree if xmlParentElt is None else xmlParentElt).xpath(eltDesc.inSelector, namespaces = eltDesc.namespaces):
            if eltDesc.outElement == targetOutElement:
                outList.append(etree.tostring(xmlElt).decode('utf8'))
            elif eltDesc.children:
                for childDesc in eltDesc.children:
                    ProcessEltDesc(xmlElt, childDesc)
    keepAliveHash = BuildKeepAliveHash(xmlTree)
    ProcessEltDesc(None, mainEltDesc)
    return outList

def PruneMapping(mainEltDesc, eltAndTextValuePairs):
    """
    Returns a mapping obtained by keeping only those EMDs of 'mainEltDesc'
    which have a TMD such that the pair (EMD.outElement, TMD.outElement) is
    in eltAndTextValuePairs (which should be a set or dict), and the ancestors
    of such EMDs.  The resulting mapping could even be empty (i.e. None).
    """
    def PruneEltDesc(inEltDesc):
        newTextDescs = [textDesc for textDesc in inEltDesc.textVals or []
            if (inEltDesc.outElement, textDesc.outElement) in eltAndTextValuePairs]
        if not newTextDescs: newTextDescs = None
        newChildDescs = []
        for inChildDesc in inEltDesc.children or []:
            outChildDesc = PruneEltDesc(inChildDesc)
            if outChildDesc: newChildDescs.append(outChildDesc)
        if not newChildDescs: newChildDescs = None
        if (not newChildDescs) and (not newTextDescs): return None
        outEltDesc = TEltDesc(None)
        outEltDesc.inSelector = inEltDesc.inSelector
        outEltDesc.outElement = inEltDesc.outElement
        outEltDesc.jsonPlural = inEltDesc.jsonPlural
        outEltDesc.children = newChildDescs
        outEltDesc.textVals = newTextDescs
        outEltDesc.namespaces = inEltDesc.namespaces
        return outEltDesc
    return PruneEltDesc(mainEltDesc)

def ExtractTextValues(xmlTree, mainEltDesc, destDict):
    """
    'destDict' should be a dict where keys are pairs of strings.  Such a key, say ("x", "y"),
    represents a request that this function should gather all distinct strings that occur
    as the text value "y" of the output element "x" if the input XML tree 'xmlTree' is
    transformed using the mapping 'mainEltDesc'.  A set of these strings will be stored in
    'destDict' as the value corresponding to this key.
    """
    if not destDict or not mainEltDesc: return
    destDictKeys = set(destDict.keys())
    for key in destDictKeys:
        if destDict[key] is None: destDict[key] = set()
    # Process the input XML tree.
    autogenCounters = {}
    def ProcessTextDesc(xmlElt, eltDesc, textDesc):
        if textDesc.inSelector is None: xmlElts = [xmlElt]
        else:
            L = xmlElt.xpath(textDesc.inSelector, namespaces = textDesc.namespaces)
            if not L: return
            xmlElt = L[0]
        # Prepare the value of the attribute from which this text item is to be taken.
        eltStartPos = {}; eltEndPos = {}
        if textDesc.attribute == ATTR_CONSTANT:
            attrValue = textDesc.constant
        elif textDesc.attribute == ATTR_AUTO:
            key = eltDesc.outElement + "." + textDesc.outElement
            seqNo = autogenCounters.get(key, 0) + 1
            autogenCounters[key] = seqNo
            attrValue = "%s.%d" % (key, seqNo)
        elif textDesc.attribute == ATTR_INNER_TEXT or textDesc.attribute == ATTR_INNER_TEXT_REC:
            attrValue = BuildInnerText(xmlElt, eltStartPos, eltEndPos, textDesc.attribute == ATTR_INNER_TEXT_REC)
        else:
            attrValue = xmlElt.attrib.get(textDesc.attribute, None)
            if attrValue is None: return
        # Match 'attrValue' against the regex if needed.
        if textDesc.compiledRegex is None:
            matchStart = 0; matchEnd = len(attrValue)
        else:
            m = textDesc.compiledRegex.search(attrValue)
            if not m: return
            grp = 0 if textDesc.regexGroup is None else textDesc.regexGroup
            try:  matchStart = m.start(grp); matchEnd = m.end(grp)
            except: pass # must be an error in the mapping, perhaps an invalid group name
            if matchStart < 0 or matchEnd < 0: return # the group did not contribute to the match
        # Store the matched value in the destination dictionary.
        matchLen = matchEnd - matchStart; outValue = attrValue[matchStart:matchEnd]
        key = (eltDesc.outElement, textDesc.outElement)
        if key in destDict: destDict[key].add(outValue)
    def ProcessEltDesc(xmlParentElt, eltDesc):
        for xmlElt in (xmlTree if xmlParentElt is None else xmlParentElt).xpath(eltDesc.inSelector, namespaces = eltDesc.namespaces):
            if eltDesc.children:
                for childDesc in eltDesc.children:
                    ProcessEltDesc(xmlElt, childDesc)
            if eltDesc.textVals:
                for textDesc in eltDesc.textVals:
                    ProcessTextDesc(xmlElt, eltDesc, textDesc)
    keepAliveHash = BuildKeepAliveHash(xmlTree)
    ProcessEltDesc(None, mainEltDesc)

class JsonToXmlSettings:
    mapPluralToSingular = {"forPartsOfSpeech": "forPartOfSpeech", "sameAs": "sameAs", "partsOfSpeech": "partOfSpeech", "etymologies": "etymology", "entries": "entry"}
    saveAsAttribute = {
        "lexicographicResource": ["title", "uri", "langCode"],
        "entry": ["id", "homographNumber"],
        "partOfSpeech": ["tag"],
        "inflectedForm": ["tag"],
        "sense": ["id"],
        "definition": ["definitionType"],
        "label": ["tag"],
        "pronunciation": ["soundFile"],
        "transcription": ["scheme"],
        "example": ["sourceIdentity", "sourceElaboration", "soundFile"],
        "translationLanguage": ["langCode"],
        "headwordTranslation": ["langCode"],
        "headwordExplanation": ["langCode"],
        "exampleTranslation": ["langCode", "soundFile"],
        "partOfSpeechTag": ["tag", "forHeadwords", "forTranslations","forEtymology"],
        "inflectedFormTag": ["tag", "forHeadwords", "forTranslations"],
        "definitionTypeTag": ["tag"],
        "labelTag": ["tag", "typeTag", "forHeadwords", "forTranslations", "forCollocates"],
        "labelTypeTag": ["tag"],
        "sourceIdentityTag": ["tag"],
        "transcriptionSchemeTag": ["tag", "forHeadwords", "forTranslations"],
        "forLanguage": ["langCode"],
        "forPartOfSpeech": ["tag"],
        "sameAs": ["url"],
        "relation": ["type"],
        "member": ["memberID", "role", "obverseListingOrder"],
        "relationType": ["type", "scopeRestriction"],
        "memberType": ["role", "type", "min", "max", "hint"],
        "placeholderMarker": ["startIndex", "endIndex"],
        "headwordMarker": ["startIndex", "endIndex"],
        "collocateMarker": ["startIndex", "endIndex", "id", "lemma"],
        "etymology": [],
        "etymon": ["when", "type"],
        "etymonUnit": ["langCode", "reconstructed"],
        "etymonType": ["type"],
        "etymonLanguage": ["langCode"]
    }
    # Each element may have up to one property that is saved as the inner text.
    saveAsInnerText = {"definition": "text", "transcription": "text", "headwordExplanation": "text"}
    # Which property do markers apply to?  Maps (element, property) to a list of marker names.
    propertyToMarkers = {
        ("entry", "headword"): ["placeholderMarker"],
        ("headwordTranslation", "text"): ["placeholderMarker"],
        ("definition", "text"): ["headwordMarker", "collocateMarker"],
        ("example", "text"): ["headwordMarker", "collocateMarker"],
        ("exampleTranslation", "text"): ["headwordMarker", "collocateMarker"]
    }
    markersToProperty = { (element, marker): property for ((element, property), markers) in propertyToMarkers.items() for marker in markers }
    @classmethod
    def ToSingular(cls, propName):
        if propName in cls.mapPluralToSingular: return cls.mapPluralToSingular[propName]
        elif propName.endswith('s'): return propName[:-1]
        else: return propName

def ConvertDmLexJsonToXml(inJsonRoot):
    """
    'inJsonRoot' should be a dict containing a JSON representation of a DMLex tree.
    This function constructs an XML representation of the same DMLex tree
    and returns its root element (of type 'etree.Element').
    """
    indent = "    "; newLine = "\n"
    def ProcessElement(inJson, outEltName, depth, skipStartEndIdx, noWhitespace):
        assert type(inJson) is dict
        saveAsAttribute = JsonToXmlSettings.saveAsAttribute.get(outEltName, {})
        childToPromote = None; children = []
        # Figure out which properties are markers and will need to be processed differently.
        markerProps = set(); textToMarkers = {}
        for propName in inJson:
            propNameSg = JsonToXmlSettings.ToSingular(propName)
            textPropName = JsonToXmlSettings.markersToProperty.get((outEltName, propNameSg), None)
            if not textPropName: continue # evidently 'propName' is not a marker
            if textPropName not in inJson: continue # 'propName' is a marker but the corresponding text property is not present, so we'll treat the 'marker' as a regular property
            markerProps.add(propName)
            if textPropName not in textToMarkers: textToMarkers[textPropName] = [propName]
            else: textToMarkers[textPropName].append(propName)
        # Process all the properties.
        attributes = {}
        for propName, propValue in inJson.items():
            if propName in markerProps: continue
            if skipStartEndIdx and (propName == "startIndex" or propName == "endIndex"): continue
            # Some properties must be saved as attributes.
            if propName in saveAsAttribute: attributes[propName] = str(propValue); continue
            # Otherwise we'll save it as a child element.
            # If the value is an array, we'll create multiple child elements.
            if type(propValue) is list:
                # Determine the singular form of the property name.
                propNameSg = JsonToXmlSettings.ToSingular(propName)
                for propValue2 in propValue: children.append(ProcessElement(propValue2, propNameSg, depth + 1, False, noWhitespace))
                continue
            # Otherwise we'll create a single child element.  Perhaps it's an object.
            if type(propValue) is dict:
                children.append(ProcessElement(propValue, propName, depth + 1, False, noWhitespace))
                continue
            # Otherwise it must be some sort of string value.
            propValue = str(propValue)
            childElt = etree.Element(propName)
            # Perhaps this string value has to actually be stored in the inner text of the current element instead of as a child?
            if JsonToXmlSettings.saveAsInnerText.get(outEltName, None) == propName: childToPromote = childElt
            # See which markers apply to this value.
            class TMarker:
                __slots__ = ["iStart", "iEnd", "propNameSg", "propValue", "childElt", "nestedMarkers"]
                def __init__(self, propNameSg, propValue):
                    self.iStart = propValue["startIndex"]; self.iEnd = propValue["endIndex"]
                    self.propNameSg = propNameSg; self.propValue = propValue
                    self.childElt = None; self.nestedMarkers = []
                def PopulateChild(self, s):
                    if not self.nestedMarkers: self.childElt.text = s[self.iStart:self.iEnd]; return
                    curPos = self.iStart; lastChild = None
                    for m in self.nestedMarkers:
                        assert curPos <= m.iStart
                        if lastChild is not None: lastChild.tail = s[curPos:m.iStart]
                        else: self.childElt.text = s[curPos:m.iStart]
                        m.PopulateChild(s); curPos = m.iEnd
                        lastChild = m.childElt; self.childElt.append(lastChild)
                    assert curPos <= self.iEnd; assert lastChild is not None
                    lastChild.tail = s[curPos:self.iEnd]
            markers = []
            for markerPropName in textToMarkers.get(propName, []):
                markerPropNameSg = JsonToXmlSettings.ToSingular(markerPropName)
                for markerJson in inJson[markerPropName]: markers.append(TMarker(markerPropNameSg, markerJson))
            # Sort the markers in increasing order of starting position; those with the same starting
            # position will be sorted in decreasing order of the ending position.
            markers.sort(key = (lambda m: (m.iStart, -m.iEnd)))
            root = TMarker(None, {"startIndex": 0, "endIndex": len(propValue)}); stack = [root]
            for m in markers:
                # Invariant: the stack contains 0 or more markers, each of which can be nested in the previous one.
                # Can 'm' be added to the stack?
                # - Perhaps 'm' has an invalid start/end index.
                if not (0 <= m.iStart <= m.iEnd <= len(propValue)): continue
                # - Perhaps some of the markers at the top of the stack end before 'm' begins; they can be closed and popped off the stack.
                while len(stack) > 1 and stack[-1].iEnd <= m.iStart: stack.pop()
                # - See if 'm' can be nested in the marker that is now at the top of the stack.
                if stack[-1].iStart <= m.iStart and m.iEnd <= stack[-1].iEnd:
                    stack[-1].nestedMarkers.append(m)
                    stack.append(m)
                    m.childElt = ProcessElement(m.propValue, m.propNameSg, depth + 1, True, True)
            # Construct the nested structure with strings in suitable places.
            root.childElt = childElt; root.PopulateChild(propValue)
            children.append(childElt)
            # Any markers that weren't included in the nested structure will be appended as children.
            # Let's group them by property name.
            markers.sort(key = (lambda m: (m.propNameSg, m.iStart, -m.iEnd)))
            for m in markers:
                if m.childElt is None: children.append(ProcessElement(m.propValue, m.propNameSg, depth + 1, False, noWhitespace))
        # Promote the content of the child that needs to be promoted.
        if childToPromote is not None: outElt = childToPromote; outElt.tag = outEltName; noWhitespace = True
        else: outElt = etree.Element(outEltName)
        # Add the attributes.
        for (attrName, attrValue) in attributes.items(): outElt.set(attrName, attrValue)
        # Append the other children.
        lastChild = None; newIndent = newLine + indent * (depth + 1)
        for child in children:
            if child is childToPromote: continue
            if not noWhitespace:
                if lastChild is not None: lastChild.tail = newIndent
                elif not outElt.text: outElt.text = newIndent
                else: outElt.text += newIndent
            outElt.append(child); lastChild = child
        if lastChild is not None: lastChild.tail = newLine + indent * depth
        return outElt
    return ProcessElement(inJsonRoot, "lexicographicResource", 0, False,
False)

def ConvertDmLexJsonToXml_File(fnInJson, fnOutXml):
    """
    Reads a JSON representation of a DMLex tree from the file named 'fnInJson'
    and saves its XML representation into a file named 'fnOutXml'.
    If the file 'fnInJson' contains an array of several DMLex trees,
    they will all be converted and their <lexicographicResource> elements will
    be wrapped in an element named <root>.
    """
    with open(fnInJson, "rt") as f:
        inJson = json.load(f)
    xmlTrees = []
    if type(inJson) is list:
        root = etree.Element("root")
        first = True
        for jsTree in inJson:
            assert type(jsTree) is dict
            if first: root.text = '\n\n'; first = False
            child = ConvertDmLexJsonToXml(jsTree)
            child.tail = '\n\n'
            root.append(child)
    else:
        assert type(jsTree) is dict
        root = ConvertDmLexJsonToXml(fnInJson)
    outTree = etree.ElementTree(root)
    with open(fnOutXml, "wb") as f:
        f.write(etree.tostring(outTree, encoding = "utf8", xml_declaration = True))

def TransformEx(
        mappingJsonFn = None, mappingJsonStr = None, mappingJson = None, mappingEltDesc = None,
        fnOrFileList = None, xmlStringList = None, treeList = None,
        fnOutJson = None, fnOutXml = None, prettyPrint = False, extractXmlInElementsForOutElement = None,
        extractTextValues = None):
    """
    This function converts one or more XML documents into DMLex trees and returns a list of these as JSON-like objects.

    The description of the mapping can either be provided in a file (whose name is given by 'mappingJsonFn'),
    or in a string (provided by 'mappingJsonStr', which will be parsed into JSON),
    or in a JSON-like object (i.e. a dict, provided by 'mappingJson'),
    or as a TEltDesc object (provided by 'mappingEltDesc').

    The input XML document(s) can be one or more of the following:
    - 'fnOrFileList' = a list of file names and/or file-like objects;
    - 'xmlStringList' = a string containing the contents of an XML document, or a list of several such strings;
    - 'treeList' = a list of XML trees (etree.ElementTree objects).

    The resulting DMLex tree(s) can optionally be saved into the file named 'fnOutJson', in JSON format,
    if this parameter is provided.  'prettyPrint' can optionally be used to have this file
    formatted with indentation to make it more readable.

    The DMLex tree(s) can optionally also be saved into the file named 'fnOutXml', in XML format.

    If 'extractTextValues' is nonempty, this function does not actually perform a mapping.
    In this case 'extractTextValues' should be a dictionary whose keys are pairs of strings
    and the corresponding values should be empty sets.  Each such key, e.g. ("x", "y"),
    constitutes a request that all the text values named "y" of all the output elements named "x"
    should be added to the set 'extractTextValues["x", "y"]', where they will be available
    to the caller when TransformEx returns.  Note that in this case, regex matching is performed
    but xlat mapping is not (even if xlat dictionaries are present in the input mapping).

    If 'extractXmlInElementsForOutElement' is nonempty, this function does not actually perform
    a mapping, but prepares a string serialization of every input XML element to which an
    element mapping description whose 'outElement' equals 'extractXmlInElementsForOutElement'
    would be applied.  The function then returns a list of these string serializations.
    """
    # Read the mapping description.
    if mappingEltDesc is None:
        if mappingJson is not None:
            mappingEltDesc = TEltDesc(mappingJson)
        elif mappingJsonStr is not None:
            mappingJson = json.loads(mappingJsonStr)
            mappingEltDesc = TEltDesc(mappingJson)
        elif mappingJsonFn is not None:
            logging.info("Loading the mapping description from \"%s\"." % mappingJsonFn)
            with open(mappingJsonFn, "rt") as f:
                mappingJson = json.load(f)
            mappingEltDesc = TEltDesc(mappingJson)
        else:
            logging.error("No mapping has been provided."); return
    # For the extractTextValues functionality, we'll prune the mapping to keep
    # only the parts needed to extract the requested values.
    if extractTextValues:
        mappingEltDesc = PruneMapping(mappingEltDesc, extractTextValues)
        if not mappingEltDesc: return
    # Process all the input XML files.
    outJson = []
    def ProcessFile(fn, f):
        logging.info("Parsing \"%s\"." % fn)
        tree = etree.parse(f)
        logging.info("Transforming \"%s\"." % fn)
        nonlocal outJson
        if extractTextValues: ExtractTextValues(tree, mappingEltDesc, extractTextValues)
        elif extractXmlInElementsForOutElement: outJson += ExtractEntries(tree, mappingEltDesc, extractXmlInElementsForOutElement)
        else: outJson += TransformSingleXmlDoc(tree, mappingEltDesc)
    if fnOrFileList is None: fnOrFileList = []
    if type(fnOrFileList) is type(""): fnOrFileList = [fnOrFileList]
    for fn in [] if fnOrFileList is None else fnOrFileList:
        if type(fn) is not str:
            ProcessFile("<file-like-object>", fn)
        elif "*" in fn:
            for path in pathlib.Path(".").glob(fn):
                with open(str(path), "rb") as f: ProcessFile(str(path), f)
        elif fn.lower().endswith(".zip"):
            with zipfile.ZipFile(fn, "r") as zf:
                for fn2 in zf.namelist():
                    with zf.open(fn2, "r") as f: ProcessFile("%s[%s]" % (fn, fn2), f)
        else:
            with open(fn, "rb") as f: ProcessFile(fn, f)
    # Process all the input XML documents provided as strings.
    for xmlString in [] if xmlStringList is None else xmlStringList:
        root = etree.fromstring(xmlString)
        tree = etree.ElementTree(root)
        if extractTextValues: ExtractTextValues(tree, mappingEltDesc, extractTextValues)
        elif extractXmlInElementsForOutElement: outJson += ExtractEntries(tree, mappingEltDesc, extractXmlInElementsForOutElement)
        else: outJson += TransformSingleXmlDoc(tree, mappingEltDesc)
    # Process all the input XML trees.
    for tree in [] if treeList is None else treeList:
        if extractTextValues: ExtractTextValues(tree, mappingEltDesc, extractTextValues)
        elif extractXmlInElementsForOutElement: outJson += ExtractEntries(tree, mappingEltDesc, extractXmlInElementsForOutElement)
        else: outJson += TransformSingleXmlDoc(tree, mappingEltDesc)
    # Save the output JSON file.
    if fnOutJson:
        with open(fnOutJson, "wt", encoding = "utf8") as f:
            json.dump(outJson, f, ensure_ascii = True, indent = "    " if prettyPrint else None)
    # Save the output XML file.
    if fnOutXml:
        root = etree.Element("root"); first = True
        for js in outJson:
            if first: root.text = "\n\n"; first = False
            child = ConvertDmLexJsonToXml(js); child.tail = "\n\n"
            root.append(child)
        outTree = etree.ElementTree(root)
        with open(fnOutXml, "wb") as f:
            f.write(etree.tostring(outTree, encoding = "utf8", xml_declaration = True))
    return outJson

if __name__ == "__main__":
    if True:
        # Transform an input XML file into an output JSON file.
        #TransformEx(mappingJsonFn = "dmlex\\0_dmlex_transform.json",fnOrFileList = ["dmlex\\samples\\0_dictionary_-tiny.xml"], fnOutJson ="dmlex\\0_dmlex_out.json", prettyPrint = True, fnOutXml ="dmlex\\0_dmlex_out.xml")
        TransformEx(mappingJsonFn = "dmlex\\kks_dmlex_transform.json",fnOrFileList = ["dmlex\\samples\\kks_18_suus-tiny.xml"], fnOutJson = "dmlex\\kks_dmlex_out.json", prettyPrint = True, fnOutXml = "dmlex\\kks_dmlex_out.xml")
    if False:
        # Extract those elements of the input XML file which would be mapped into 'entry' elements
        # in the output JSON file if we applied the transformation in the usual way.
        # These extracted XML characters are serialized as strings, and a list of such strings is returned.
        listOfStrings = TransformEx(mappingJsonFn = "dmlex\\kks_dmlex_transform.json", fnOrFileList = "dmlex\\samples\\kks_18_suus-tiny.xml", extractXmlInElementsForOutElement = "entry")
        for s in listOfStrings: logging.info(s)
    if False:
        # Extract certain text values and store them into sets in a dictionary.
        destDict = { ("entry", "partOfSpeech"): set(), ("definition", "value"): set() }
        TransformEx(mappingJsonFn = "dmlex\\kks_dmlex_transform.json", fnOrFileList = "dmlex\\samples\\kks_18_suus-tiny.xml", extractTextValues = destDict)
        logging.info("Possible text-values of entry.partOfSpeech: %s" % destDict["entry", "partOfSpeech"])
        logging.info("Possible text-values of definition.value: %s" % destDict["definition", "value"])