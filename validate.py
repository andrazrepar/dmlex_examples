import xmlschema

def validate_xml(xml_path: str, xsd_path: str) -> None:
    # Load the XSD file using xmlschema
    my_schema = xmlschema.XMLSchema11(xsd_path)

    # Create an XMLResource with the XML file
    xml_resource = xmlschema.XMLResource(xml_path)

    # Validate the XML against the XSD using the resource
    try:
        my_schema.validate(xml_resource)
        print("XML is valid.")
    except xmlschema.validators.exceptions.XMLSchemaValidationError as e:
        print("XML is invalid.")
        print(e)
    except xmlschema.validators.exceptions.XMLSchemaValidatorError as e:
        print("Error parsing the schema or XML:")
        print(e)
    except Exception as e:
        print("An unexpected error occurred:")
        print(e)

# Example usage
xml_path = 'training_data/rilmta/rilmta_out.xml'
xsd_path = 'dmlex.xsd'
validate_xml(xml_path, xsd_path)