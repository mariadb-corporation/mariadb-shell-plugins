# Copyright (c) 2023, 2026, Oracle and/or its affiliates.
# Copyright (c) 2026, MariaDB plc.
# MySQL Workbench Plugin
# Module with Audit Log Trigger function
# Written in MySQL Workbench 8.0.26

from wb import *
import grt

# import mforms

ModuleInfo = DefineModule(
    "Audit_Log_Triggers",
    author="MikeZ",
    version="1.2",
    description="Contains Plugin Audit_Log_Triggers",
)


def get_wb_doc_dir(filename, *argv):
    """Returns the doc dir of the current model

    Args:
        *argv: The list of directories to be added to the path

    Returns:
        The plugin directory path as string
    """
    import os.path, platform

    home_dir = os.path.expanduser("~")
    if not home_dir:
        raise Exception("No home directory set")
    os_name = platform.system()
    if os_name == "Windows":
        wb_dir = os.path.join(
            home_dir, "AppData", "Roaming", "MySQL", "Workbench", filename + "d"
        )
    else:
        wb_dir = os.path.join(
            home_dir,
            "Library",
            "Application Support",
            "MySQL",
            "Workbench",
            filename + "d",
        )

    for arg in argv:
        wb_dir = os.path.join(wb_dir, arg)

    return wb_dir


def create_audit_log_table():
    import datetime

    # iterate through all tables from schema
    schema = grt.root.wb.doc.physicalModels[0].catalog.schemata[0]

    # Check if audit_log table is already in the schema
    audit_log_table = False
    for table in schema.tables:
        if table.name == "audit_log":
            audit_log_table = True
            break
    if not audit_log_table:
        audit_table = schema.addNewTable("db.mysql")  # grt.classes.db_mysql_Table()
        audit_table.owner = grt.root.wb.doc.physicalModels[0].catalog.schemata[0]
        audit_table.name = "audit_log"
        audit_table.oldName = "audit_log"
        audit_table.tableEngine = "InnoDB"
        audit_table.createDate = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}"
        audit_table.lastChangeDate = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}"
        # Column ---------
        c_id = grt.classes.db_mysql_Column()
        c_id.autoIncrement = 1
        c_id.formattedType = "INT"
        c_id.isNotNull = 1
        c_id.length = -1
        c_id.name = "id"
        c_id.oldName = "id"
        c_id.precision = -1
        c_id.scale = -1
        c_id.owner = audit_table
        c_id.simpleType = grt.root.wb.doc.physicalModels[0].catalog.simpleDatatypes[3]
        audit_table.addColumn(c_id)
        # Column ---------
        c_t_n = grt.classes.db_mysql_Column()
        c_t_n.autoIncrement = 0
        c_t_n.formattedType = "VARCHAR(255)"
        c_t_n.isNotNull = 1
        c_t_n.length = 255
        c_t_n.name = "table_name"
        c_t_n.oldName = "table_name"
        c_t_n.precision = -1
        c_t_n.scale = -1
        c_t_n.owner = audit_table
        c_t_n.simpleType = grt.root.wb.doc.physicalModels[0].catalog.simpleDatatypes[11]
        audit_table.addColumn(c_t_n)
        # Column ---------
        c = grt.classes.db_mysql_Column()
        c.autoIncrement = 0
        c.datatypeExplicitParams = "('INSERT','UPDATE','DELETE')"
        c.formattedType = "ENUM('INSERT','UPDATE','DELETE')"
        c.isNotNull = 1
        c.length = -1
        c.name = "dml_type"
        c.oldName = "dml_type"
        c.precision = -1
        c.scale = -1
        c.owner = audit_table
        c.simpleType = grt.root.wb.doc.physicalModels[0].catalog.simpleDatatypes[42]
        audit_table.addColumn(c)
        # Column ---------
        c = grt.classes.db_mysql_Column()
        c.autoIncrement = 0
        c.formattedType = "JSON"
        c.isNotNull = 0
        c.length = -1
        c.name = "old_row_data"
        c.oldName = "old_row_data"
        c.precision = -1
        c.scale = -1
        c.owner = audit_table
        c.simpleType = grt.root.wb.doc.physicalModels[0].catalog.simpleDatatypes[23]
        audit_table.addColumn(c)
        # Column ---------
        c = grt.classes.db_mysql_Column()
        c.autoIncrement = 0
        c.formattedType = "JSON"
        c.isNotNull = 0
        c.length = -1
        c.name = "new_row_data"
        c.oldName = "new_row_data"
        c.precision = -1
        c.scale = -1
        c.owner = audit_table
        c.simpleType = grt.root.wb.doc.physicalModels[0].catalog.simpleDatatypes[23]
        audit_table.addColumn(c)
        # Column ---------
        c_c_b = grt.classes.db_mysql_Column()
        c_c_b.autoIncrement = 0
        c_c_b.formattedType = "VARCHAR(255)"
        c_c_b.isNotNull = 1
        c_c_b.length = 255
        c_c_b.name = "changed_by"
        c_c_b.oldName = "changed_by"
        c_c_b.precision = -1
        c_c_b.scale = -1
        c_c_b.owner = audit_table
        c_c_b.simpleType = grt.root.wb.doc.physicalModels[0].catalog.simpleDatatypes[11]
        audit_table.addColumn(c_c_b)
        # Column ---------
        c_c_a = grt.classes.db_mysql_Column()
        c_c_a.autoIncrement = 0
        c_c_a.formattedType = "TIMESTAMP"
        c_c_a.isNotNull = 1
        c_c_a.length = -1
        c_c_a.name = "changed_at"
        c_c_a.oldName = "changed_at"
        c_c_a.precision = -1
        c_c_a.scale = -1
        c_c_a.owner = audit_table
        c_c_a.simpleType = grt.root.wb.doc.physicalModels[0].catalog.simpleDatatypes[31]
        audit_table.addColumn(c_c_a)
        # Column ---------
        c_o_r_i = grt.classes.db_mysql_Column()
        c_o_r_i.autoIncrement = 0
        c_o_r_i.expression = 'JSON_EXTRACT(old_row_data, "$.id")'
        c_o_r_i.formattedType = "INT"
        c_o_r_i.generated = 1
        c_o_r_i.isNotNull = 0
        c_o_r_i.length = -1
        c_o_r_i.name = "old_row_id"
        c_o_r_i.oldName = "old_row_id"
        c_o_r_i.precision = -1
        c_o_r_i.scale = -1
        c_o_r_i.owner = audit_table
        c_o_r_i.simpleType = grt.root.wb.doc.physicalModels[0].catalog.simpleDatatypes[
            3
        ]
        audit_table.addColumn(c_o_r_i)
        # Column ---------
        c_n_r_i = grt.classes.db_mysql_Column()
        c_n_r_i.autoIncrement = 0
        c_n_r_i.expression = 'JSON_EXTRACT(new_row_data, "$.id")'
        c_n_r_i.formattedType = "INT"
        c_n_r_i.generated = 1
        c_n_r_i.isNotNull = 0
        c_n_r_i.length = -1
        c_n_r_i.name = "new_row_id"
        c_n_r_i.oldName = "new_row_id"
        c_n_r_i.precision = -1
        c_n_r_i.scale = -1
        c_n_r_i.owner = audit_table
        c_n_r_i.simpleType = grt.root.wb.doc.physicalModels[0].catalog.simpleDatatypes[
            3
        ]
        audit_table.addColumn(c_n_r_i)

        # Index ---------
        i = grt.classes.db_mysql_Index()
        i.name = "PRIMARY"
        i.oldName = "PRIMARY"
        i.indexType = "PRIMARY"
        i.owner = audit_table
        i.visible = 1
        i.isPrimary = 1
        # Index Column ---------
        ic = grt.classes.db_mysql_IndexColumn()
        ic.owner = i
        ic.referencedColumn = c_id
        i.columns.append(ic)
        audit_table.addIndex(i)
        audit_table.primaryKey = i

        # Index ---------
        i = grt.classes.db_mysql_Index()
        i.name = "idx_table_name"
        i.oldName = "idx_table_name"
        i.indexType = "INDEX"
        i.owner = audit_table
        i.visible = 1
        i.isPrimary = 0
        # Index Column ---------
        ic = grt.classes.db_mysql_IndexColumn()
        ic.owner = i
        ic.referencedColumn = c_t_n
        i.columns.append(ic)
        audit_table.addIndex(i)

        # Index ---------
        i = grt.classes.db_mysql_Index()
        i.name = "idx_changed_at"
        i.oldName = "idx_changed_at"
        i.indexType = "INDEX"
        i.owner = audit_table
        i.visible = 1
        i.isPrimary = 0
        # Index Column ---------
        ic = grt.classes.db_mysql_IndexColumn()
        ic.owner = i
        ic.referencedColumn = c_c_a
        i.columns.append(ic)
        audit_table.addIndex(i)

        # Index ---------
        i = grt.classes.db_mysql_Index()
        i.name = "idx_changed_by"
        i.oldName = "idx_changed_by"
        i.indexType = "INDEX"
        i.owner = audit_table
        i.visible = 1
        i.isPrimary = 0
        # Index Column ---------
        ic = grt.classes.db_mysql_IndexColumn()
        ic.owner = i
        ic.referencedColumn = c_c_b
        i.columns.append(ic)
        audit_table.addIndex(i)

        # Index ---------
        i = grt.classes.db_mysql_Index()
        i.name = "idx_new_row_id"
        i.oldName = "idx_new_row_id"
        i.indexType = "INDEX"
        i.owner = audit_table
        i.visible = 1
        i.isPrimary = 0
        # Index Column ---------
        ic = grt.classes.db_mysql_IndexColumn()
        ic.owner = i
        ic.referencedColumn = c_n_r_i
        i.columns.append(ic)
        audit_table.addIndex(i)

        # Index ---------
        i = grt.classes.db_mysql_Index()
        i.name = "idx_old_row_id"
        i.oldName = "idx_old_row_id"
        i.indexType = "INDEX"
        i.owner = audit_table
        i.visible = 1
        i.isPrimary = 0
        # Index Column ---------
        ic = grt.classes.db_mysql_IndexColumn()
        ic.owner = i
        ic.referencedColumn = c_o_r_i
        i.columns.append(ic)
        audit_table.addIndex(i)


# This plugin takes no arguments
@ModuleInfo.plugin(
    "Audit_Log_Triggers",
    caption="Generate audit_log Table and Trigger Script",
    description="Generates an audit_log table and the corresponding Trigger statements for all tables in the model as an SQL script",
    input=[],
    pluginMenu="Utilities",
)
@ModuleInfo.export(grt.INT)
def Audit_Log_Triggers():
    import datetime

    # Create audit_log table if it has not been created yet
    create_audit_log_table()

    # iterate through all tables from schema
    schema = grt.root.wb.doc.physicalModels[0].catalog.schemata[0]

    # Check if the audit_log.old_row_id is a generated column
    generated_id_cols = False
    for table in schema.tables:
        if table.name == "audit_log":
            for col in table.columns:
                if col.name == "old_row_id":
                    generated_id_cols = col.generated == 1
                    break
            break

    sql_script = """-- -----------------------------------------------------
-- Create audit_log triggers

"""

    sql_script += f"DELIMITER %%\n\n"
    trigger_footer = f"""
        SESSION_USER(),
        CURRENT_TIMESTAMP
    );
END%%"""

    # Loop over all schema tables
    for table in schema.tables:
        if table.name == "audit_log":
            continue
        if "no_audit_log" in table.comment:
            continue
        insert_header = """
    INSERT INTO `mysql_rest_service_metadata`.`audit_log` (
        table_name, dml_type, old_row_data, new_row_data"""
        if not generated_id_cols:
            insert_header += ", old_row_id, new_row_id"
        insert_header += """, changed_by, changed_at)
    VALUES ("""
        insert_trigger = (
            f"DROP TRIGGER IF EXISTS `{table.name}_AFTER_INSERT_AUDIT_LOG`%%\n"
            f"CREATE TRIGGER `{table.name}_AFTER_INSERT_AUDIT_LOG`\n"
            f"    AFTER INSERT ON `{table.name}` FOR EACH ROW\nBEGIN{insert_header}\n"
            f'{" ":8}"{table.name}", \n{" ":8}"INSERT", \n{" ":8}NULL,\n{" ":8}JSON_OBJECT(\n'
        )
        update_trigger = (
            f"DROP TRIGGER IF EXISTS `{table.name}_AFTER_UPDATE_AUDIT_LOG`%%\n"
            f"CREATE TRIGGER `{table.name}_AFTER_UPDATE_AUDIT_LOG`\n"
            f"    AFTER UPDATE ON `{table.name}` FOR EACH ROW\nBEGIN{insert_header}\n"
            f'{" ":8}"{table.name}", \n{" ":8}"UPDATE", \n{" ":8}JSON_OBJECT(\n'
        )
        delete_trigger = (
            f"DROP TRIGGER IF EXISTS `{table.name}_AFTER_DELETE_AUDIT_LOG`%%\n"
            f"CREATE TRIGGER `{table.name}_AFTER_DELETE_AUDIT_LOG`\n"
            f"    AFTER DELETE ON `{table.name}` FOR EACH ROW\nBEGIN{insert_header}\n"
            f'{" ":8}"{table.name}", \n{" ":8}"DELETE", \n{" ":8}JSON_OBJECT(\n'
        )
        old_rows = ""
        new_rows = ""
        for column in table.columns:
            data_type = column.formattedRawType.upper()
            if not ("BLOB" in data_type or "TEXT" in data_type):
                if "BIT(1)" in data_type:
                    old_rows += f'{" ":12}"{column.name}", IF(OLD.{column.name}, 1, 0),\n'
                    new_rows += f'{" ":12}"{column.name}", IF(NEW.{column.name}, 1, 0),\n'
                elif "BINARY" in data_type:
                    old_rows += f'{" ":12}"{column.name}", CONCAT("0x", HEX(OLD.{column.name})),\n'
                    new_rows += f'{" ":12}"{column.name}", CONCAT("0x", HEX(NEW.{column.name})),\n'
                else:
                    old_rows += f'{" ":12}"{column.name}", OLD.{column.name},\n'
                    new_rows += f'{" ":12}"{column.name}", NEW.{column.name},\n'

        old_rows = old_rows[:-2] + "),"
        new_rows = new_rows[:-2] + "),"

        # Check if table has at least one PK column, if so use the first column for old_row_id/new_row_id
        old_row_id = "NULL,"
        new_row_id = "NULL,"
        if len(table.primaryKey.columns) >= 1:
            pk_column = table.primaryKey.columns[0].referencedColumn
            if pk_column.formattedRawType.upper() == UUID_TYPE_NAME:
                old_row_id = f"OLD.{pk_column.name},"
                new_row_id = f"NEW.{pk_column.name},"
            else:
                # audit_log.old_row_id/new_row_id are UUIDs; an integer key
                # (config) is stored as a UUID with the number in its low
                # bytes, so MariaDB does not reject the value.
                old_row_id = f"CAST(LPAD(HEX(OLD.{pk_column.name}), 32, '0') AS UUID),"
                new_row_id = f"CAST(LPAD(HEX(NEW.{pk_column.name}), 32, '0') AS UUID),"

        insert_trigger += (
            new_rows
            + (f'\n{" ":8}NULL,\n{" ":8}{new_row_id}' if not generated_id_cols else "")
            + trigger_footer
        )
        update_trigger += (
            old_rows
            + f'\n{" ":8}JSON_OBJECT(\n'
            + new_rows
            + (
                f'\n{" ":8}{old_row_id}\n{" ":8}{new_row_id}'
                if not generated_id_cols
                else ""
            )
            + trigger_footer
        )
        delete_trigger += (
            old_rows
            + f'\n{" ":8}NULL,'
            + (f'\n{" ":8}{old_row_id}\n{" ":8}NULL,' if not generated_id_cols else "")
            + trigger_footer
        )

        sql_script += (
            insert_trigger + "\n\n" + update_trigger + "\n\n" + delete_trigger + "\n\n"
        )

    sql_script += "DELIMITER ;\n"

    # Check if the script is already registered in the model
    scripts = grt.root.wb.doc.physicalModels[0].scripts
    file_name = None
    for script in scripts:
        if script.name == "Audit Log Triggers":
            file_name = script.filename[9:]
            script.lastChangeDate = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}"

    # If there is no 'Audit Log Triggers' script yet, add it
    if not file_name:
        file_name = "audit_log_triggers.sql"
        script = grt.classes.db_Script()
        script.name = "Audit Log Triggers"
        script.forwardEngineerScriptPosition = "bottom_file"
        script.createDate = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}"
        script.lastChangeDate = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}"
        script.filename = "@scripts/audit_log_triggers.sql"
        script.owner = grt.root.wb.doc.physicalModels[0]

        grt.root.wb.doc.physicalModels[0].scripts.append(script)

    # Write script to file inside the WB Doc's workdir
    import os.path

    wb_doc_script_dir = get_wb_doc_dir(
        os.path.basename(grt.root.wb.docPath), "@scripts"
    )
    if not os.path.exists(wb_doc_script_dir):
        os.makedirs(wb_doc_script_dir)
    file_path = os.path.join(wb_doc_script_dir, "audit_log_triggers.sql")

    if file_path:
        print(f"Writing file {file_path}...")
        with open(file_path, "w") as out:
            out.write(sql_script)

    return 0


# ----------------------------------------------------------------------------
# UUID columns
#
# MariaDB has a native UUID type, which Workbench's table editor refuses: it
# checks a typed-in type against its built-in MySQL grammar, and that grammar
# has no UUID. Workbench does, however, emit a *user datatype* by its SQL
# definition when forward engineering, and it shows the type by its name in
# the editor and the diagrams. So the model carries a user datatype named UUID
# whose definition is "UUID", and this plugin assigns it to the id columns -
# the only route, since the type cannot be typed in by hand.

UUID_TYPE_NAME = "UUID"
# The default of every single-column primary key: time-ordered UUIDs, so the
# keys stay index-friendly.
UUID_DEFAULT = "UUID_v7()"


def get_uuid_user_type(catalog):
    """Returns the model's UUID user datatype, creating it if it is missing.

    The actual type is BINARY, which is what the rest of Workbench falls back
    to where it needs a built-in type (the type group, the icon); the SQL
    definition is what forward engineering emits.

    Args:
        catalog: The model's db.Catalog

    Returns:
        The db.UserDatatype named UUID
    """
    for user_type in catalog.userDatatypes:
        if user_type.name.upper() == UUID_TYPE_NAME:
            return user_type

    binary = next(t for t in catalog.simpleDatatypes if t.name == "BINARY")
    user_type = grt.classes.db_UserDatatype()
    user_type.name = UUID_TYPE_NAME
    user_type.sqlDefinition = UUID_TYPE_NAME
    user_type.actualType = binary
    user_type.owner = catalog
    catalog.userDatatypes.append(user_type)
    return user_type


def apply_uuid_type(catalog):
    """Switches every BINARY(16) column of the model to the UUID user type.

    Every BINARY(16) column in this model is an id or a foreign key to one.
    A column that is a table's whole primary key also gets UUID_v7() as its
    default, unless it has a default already; foreign key columns get none.
    Columns already on the UUID type are left alone, so the plugin can be run
    again after new columns were added.

    Args:
        catalog: The model's db.Catalog

    Returns:
        A (converted, defaulted) tuple with the number of columns switched to
        UUID and the number that received the default
    """
    uuid_type = get_uuid_user_type(catalog)
    converted = 0
    defaulted = 0

    for schema in catalog.schemata:
        for table in schema.tables:
            pk_column_ids = []
            if table.primaryKey:
                pk_column_ids = [
                    index_column.referencedColumn.__id__
                    for index_column in table.primaryKey.columns
                ]

            for column in table.columns:
                is_binary16 = (
                    column.simpleType is not None
                    and column.simpleType.name == "BINARY"
                    and column.length == 16
                )
                is_uuid = (
                    column.userType is not None
                    and column.userType.__id__ == uuid_type.__id__
                )
                if not (is_binary16 or is_uuid):
                    continue

                if is_binary16:
                    column.userType = uuid_type
                    column.simpleType = None
                    # A UUID takes no length; one left over from BINARY(16)
                    # would be emitted as UUID(16).
                    column.length = -1
                    column.precision = -1
                    column.scale = -1
                    converted += 1

                is_whole_primary_key = (
                    len(pk_column_ids) == 1 and column.__id__ == pk_column_ids[0]
                )
                if is_whole_primary_key and not column.defaultValue:
                    column.defaultValue = UUID_DEFAULT
                    defaulted += 1

    return converted, defaulted


# This plugin takes no arguments
@ModuleInfo.plugin(
    "UUID_Columns",
    caption="Use the UUID type for all BINARY(16) id columns",
    description=(
        "Switches every BINARY(16) column of the model to a UUID user datatype "
        "(created if missing) and gives single-column primary keys the "
        "default UUID_v7()"
    ),
    input=[],
    pluginMenu="Utilities",
)
@ModuleInfo.export(grt.INT)
def UUID_Columns():
    catalog = grt.root.wb.doc.physicalModels[0].catalog
    converted, defaulted = apply_uuid_type(catalog)
    print(
        f"{converted} BINARY(16) column(s) switched to {UUID_TYPE_NAME}, "
        f"{defaulted} primary key column(s) given the default {UUID_DEFAULT}."
    )
    return 0
