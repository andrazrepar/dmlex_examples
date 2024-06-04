from lxml import etree
import shutil

def create_backup_and_parse_xml(xml_path):
    # Create a backup of the original XML file
    backup_path = xml_path + ".backup"
    shutil.copy(xml_path, backup_path)

    # Parse the XML file with whitespace removal
    parser = etree.XMLParser(remove_blank_text=True)
    tree = etree.parse(xml_path, parser)
    return tree

def reorder_entries(xml_path):
    tree = create_backup_and_parse_xml(xml_path)
    root = tree.getroot()

    # Define the desired order of subelements without namespace
    order = [
        "headword", "homographNumber", "partOfSpeech", "label", 
        "pronunciation", "inflectedForm", "sense"
    ]

    # Prepend namespace to each tag in the order list
    order = [f"{{{ns}}}{tag}" for tag in order]

     # Iterate over all entry elements
    for entry in root.xpath('//default:entry', namespaces={'default': ns}):
        # Store current attributes
        attributes = dict(entry.attrib)  # Use dict() to create a copy of the attributes

        # Create a dictionary to hold the subelements by tag name including namespace
        elements = {el.tag: el for el in entry}
        
        # Clear the current subelements in the entry
        entry.clear()

        # Restore attributes to the entry
        entry.attrib.update(attributes)
        
        # Append subelements in the desired order if they exist in the entry
        for tag in order:
            if tag in elements:
                entry.append(elements[tag])

    # Write the modified XML back to the file
    tree.write(xml_path, pretty_print=True, xml_declaration=True, encoding='UTF-8')

def add_homograph_numbers(xml_path):
    tree = create_backup_and_parse_xml(xml_path)
    root = tree.getroot()

     # Collect all entries and group by headword
    entries = root.xpath('//default:entry', namespaces={'default': ns})
    headword_dict = {}
    for entry in entries:
        headword = entry.find('default:headword', namespaces={'default': ns}).text
        if headword in headword_dict:
            headword_dict[headword].append(entry)
        else:
            headword_dict[headword] = [entry]

    # Assign homograph numbers
    for headword, entries in headword_dict.items():
        if len(entries) == 1:
            entries[0].set('homographNumber', '1')
        else:
            for index, entry in enumerate(entries, start=1):
                entry.set('homographNumber', str(index))

    # Write the modified XML back to the file
    tree.write(xml_path, pretty_print=True, xml_declaration=True, encoding='UTF-8')

def fix_pos_tags(xml_path):
    tree = create_backup_and_parse_xml(xml_path)
    root = tree.getroot()

    # Find existing partOfSpeechTag elements under lexicographicResource
    pos_tags = {pos_tag.get('tag') for pos_tag in root.xpath('.//default:lexicographicResource/default:partOfSpeechTag', namespaces={'default': ns})}

    # Iterate over all entries
    entries = root.xpath('//default:entry', namespaces={'default': ns})
    for entry in entries:
        pos_element = entry.find('default:partOfSpeech', namespaces={'default': ns})
        if pos_element is not None:
            pos_tag = pos_element.get('tag')
            # Check if the partOfSpeech tag is not already in partOfSpeechTag elements
            if pos_tag not in pos_tags:
                # Create a new partOfSpeechTag element and add it to lexicographicResource
                new_pos_tag = etree.Element('partOfSpeechTag')
                new_pos_tag.set('tag', pos_tag)
                root.xpath('.//default:lexicographicResource', namespaces={'default': ns})[0].append(new_pos_tag)
                pos_tags.add(pos_tag)

    # Save the modified tree back to the file or handle as needed
    tree.write(xml_path, pretty_print=True, xml_declaration=True, encoding='UTF-8')


def fix_sense_ids(xml_path):
    tree = create_backup_and_parse_xml(xml_path)
    root = tree.getroot()
    
    # Define the namespace map
    ns = 'http://docs.oasis-open.org/lexidma/ns/dmlex-1.0'
    nsmap = {'default': ns}

    entries = root.xpath('//default:entry', namespaces=nsmap)
    for entry in entries:
        entry_id = entry.get('id')

        sense_count = 1
        senses = entry.xpath('.//default:sense', namespaces=nsmap)
        for sense in senses:
            if 'id' not in sense.attrib:
                sense_id = f"{entry_id}.sense.id.{sense_count}"
                sense.set('id', sense_id)
                sense_count += 1

    # Ensure the changes are written back to the file
    tree.write(xml_path, pretty_print=True, xml_declaration=True, encoding='UTF-8', method="xml")




ns = 'http://docs.oasis-open.org/lexidma/ns/dmlex-1.0'
nsmap = {'default': ns}

filepath = 'training_data/imcs/imcs_out.xml'

# Example usage
#reorder_entries(filepath)
add_homograph_numbers(filepath)
#fix_pos_tags(filepath)
#fix_sense_ids(filepath)


