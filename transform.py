from dmlexifier import TransformEx

DIR = "training_data"
DICT = "rilmta"

TransformEx(
        # mappingJson=mapping,
        mappingJsonFn=f"{DIR}/{DICT}/spec.json",
        fnOrFileList=[
            f"{DIR}/{DICT}/dict.xml"
        ],
        # xmlStringList=[xmlString],
        fnOutJson=f"{DIR}/{DICT}/{DICT}_out.json",
        fnOutXml=f"{DIR}/{DICT}/{DICT}_out.xml",
        prettyPrint=True,
    )