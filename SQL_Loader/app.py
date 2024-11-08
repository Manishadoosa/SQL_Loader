from flask import Flask, request, render_template
import pandas as pd
import mysql.connector
import os
import json
import re

app = Flask(__name__)

# Config for file upload
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'xlsx'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure the upload folder exists
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Database connection function
def db_connect():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="Root@123",
        database="sql_loader"
    )

# Check if the uploaded file is allowed
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Function to convert Excel data to JSON format for the stored procedure
def excel_to_json(df):
    data_json = []

    # Iterate over each row of the DataFrame
    for index, row in df.iterrows():
        row_data = {}
        for column in df.columns:
            # Sanitize column name to match the SQL structure
            sanitized_column = re.sub(r'\W+', '_', column.strip().upper())
            row_data[sanitized_column] = str(row[column]) if pd.notnull(row[column]) else None  # Store as None for NaNs
        
        data_json.append(row_data)  # Add row data to the list

    json_data = json.dumps(data_json)  # Create JSON string

    return json_data

# Function to dynamically determine SQL data types based on the DataFrame
def get_sql_column_type(value):
    if pd.isnull(value):
        return "VARCHAR(255)"  # Default for null values
    elif isinstance(value, int):
        return "INT"
    elif isinstance(value, float):
        return "DECIMAL(10, 2)"
    elif isinstance(value, pd.Timestamp):
        return "DATE"
    else:
        return "VARCHAR(255)"  # Default type for strings and other data

import time

def add_columns_if_not_exist(cursor, table_name, new_columns, df):
    cursor.execute(f"DESCRIBE {table_name}")
    existing_columns = [column[0] for column in cursor.fetchall()]
    
    columns_to_add = []
    column_with_values=[]
    for column in new_columns:
        sanitized_column = re.sub(r'\W+', '_', column.strip().upper())
        
        if sanitized_column not in existing_columns:
            sample_value = df[column].dropna().iloc[0] if not df[column].dropna().empty else None
            sql_type = get_sql_column_type(sample_value)
            # Escape the column name using backticks to avoid conflicts with reserved words
            columns_to_add.append(f"`{sanitized_column}` {sql_type}")
            column_with_values.append((sanitized_column, df[column]))

    if columns_to_add:
        alter_table_query = f"ALTER TABLE {table_name} ADD COLUMN {', ADD COLUMN '.join(columns_to_add)}"
        print(f"Altering table with query: {alter_table_query}")
        
        retries = 3  # Number of retries in case of table definition change
        while retries > 0:
            try:
                cursor.execute(alter_table_query)
                print("Table altered successfully.")
                break  # Exit the loop if successful
            except mysql.connector.Error as err:
                if err.errno == 1412:  # Error code for table definition change
                    print("Table definition changed, retrying...")
                    retries -= 1
                    time.sleep(2)  # Wait before retrying
                else:
                    print(f"Error altering table: {err}")
                    return False

        primary_key_column = 'id'  # Or the actual primary key of your table, if you have one
# If there's no primary key, consider using a combination of unique columns like:
    primary_key_column = ['DATE', 'SUPPLIER_NAME', 'PRODUCT']  # Adjust as necessary

# Modify the update query
    for sanitized_column, column_values in column_with_values:
        for index, value in column_values.iteritems():
            if pd.notna(value):
                if isinstance(primary_key_column, list):
                # Build dynamic WHERE clause based on unique columns
                   where_clause = ' AND '.join([f"`{col}` = %s" for col in primary_key_column])
                   update_query = f"UPDATE {table_name} SET `{sanitized_column}` = %s WHERE {where_clause}"
                   cursor.execute(update_query, (value, *[df[col][index] for col in primary_key_column]))
                else:
                   update_query = f"UPDATE {table_name} SET `{sanitized_column}` = %s WHERE `{primary_key_column}` = %s"
                   cursor.execute(update_query, (value, df.index[index]))

    

def create_table(df, table_name):
    columns = df.columns

    # Check for duplicate columns and adjust the names if needed
    seen_columns = {}
    unique_columns = []

    for column in columns:
        sanitized_column = re.sub(r'\W+', '_', column.strip().upper())
        if sanitized_column in seen_columns:
            counter = seen_columns[sanitized_column] + 1
            new_column = f"{sanitized_column}_{counter}"
            seen_columns[sanitized_column] = counter
        else:
            new_column = sanitized_column
            seen_columns[sanitized_column] = 1

        unique_columns.append(new_column)

    column_types = []
    for column in unique_columns:
        if "DATE" in column.upper():
            column_types.append(f"`{column}` DATE")
        elif "PRICE" in column.upper() or "AMOUNT" in column.upper():
            column_types.append(f"`{column}` DECIMAL(10, 2)")
        elif "BAGS" in column.upper() or "WEIGHT" in column.upper():
            column_types.append(f"`{column}` VARCHAR(255)")
        else:
            column_types.append(f"`{column}` VARCHAR(255)")

    create_table_query = f"CREATE TABLE {table_name} ({', '.join(column_types)})"
    
    print(f"Creating table with query: {create_table_query}")
    conn = db_connect()
    cursor = conn.cursor()

    try:
        cursor.execute(create_table_query)
        conn.commit()  # Commit should be called on `conn`
    except Exception as e:
        print(f"Error creating table: {str(e)}")
    finally:
        cursor.close()
        conn.close()


def row_exists(cursor, table_name, row_data):
    unique_columns = ['DATE', 'SUPPLIER_NAME', 'PRODUCT']  # Adjust based on your unique columns

    # Build the WHERE clause dynamically based on the unique columns
    where_clauses = []
    for column in unique_columns:
        value = row_data.get(column)
        if value is not None:
            where_clauses.append(f"`{column}` = '{value}'")

    if where_clauses:
        where_clause = " AND ".join(where_clauses)
        query = f"SELECT COUNT(*) FROM {table_name} WHERE {where_clause}"
        cursor.execute(query)
        result = cursor.fetchone()
        return result[0] > 0  # Return True if the row exists
    return False

def ensure_columns_exist(cursor, table_name, json_data):
    for row in json.loads(json_data):
        for column in row.keys():
            # Check if column exists
            cursor.execute(f"SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = '{table_name}' AND COLUMN_NAME = '{column}'")
            column_exists = cursor.fetchone()[0]

            if column_exists == 0:
                # Add column if it doesn't exist
                alter_table_query = f"ALTER TABLE {table_name} ADD COLUMN `{column}` VARCHAR(255)"
                cursor.execute(alter_table_query)

def create_table_if_not_exists(cursor, df, table_name):
    # Check if the table already exists
    cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
    table_exists = cursor.fetchone()

    if table_exists:
        print(f"Table {table_name} already exists, skipping creation.")
    else:
        # Dynamically generate CREATE TABLE based on DataFrame columns
        columns = []
        column_count = {}  # To track duplicate column names
        
        for col in df.columns:
            # Sanitize the column name
            sanitized_col = re.sub(r'\W+', '_', col.strip().upper())
            
            # Handle duplicate column names
            if sanitized_col in column_count:
                column_count[sanitized_col] += 1
                sanitized_col = f"{sanitized_col}_{column_count[sanitized_col]}"
            else:
                column_count[sanitized_col] = 0

            # Determine the column type based on the dtype of the DataFrame
            if df[col].dtype == 'int64':
                col_type = 'INT'
            elif df[col].dtype == 'float64':
                col_type = 'DECIMAL(10, 2)'
            elif df[col].dtype == 'datetime64[ns]':
                col_type = 'DATE'
            else:
                col_type = 'VARCHAR(255)'  # Default to VARCHAR for string or other types

            # Add the column name and type to the list
            columns.append(f"`{sanitized_col}` {col_type}")

        # Create the table with the dynamic column names and types
        create_table_query = f"CREATE TABLE {table_name} ({', '.join(columns)})"
        print(f"Creating table with query: {create_table_query}")
        try:
            cursor.execute(create_table_query)
            print(f"Table {table_name} created successfully.")
        except Exception as e:
            print(f"Error creating table: {e}")


def insert_data(json_data, df):
    # Use create_table_if_not_exists before inserting data
    conn = db_connect()
    cursor = conn.cursor()

    try:
        table_name = 'Created_table'
        
        # Dynamically create table if it doesn't exist
        create_table_if_not_exists(cursor, df, table_name)

        # Ensure all columns from the JSON data exist in the table
        ensure_columns_exist(cursor, table_name, json_data)

        # Define the unique columns that will be used to check for row existence
        unique_columns = ['DATE', 'SUPPLIER_NAME', 'PRODUCT']  # Adjust based on your table structure

        for row in json.loads(json_data):
            if row_exists(cursor, table_name, row):
                print("Row exists, updating new columns for:", row)
                for sanitized_column, value in row.items():
                    # Skip updating unique columns or columns that are already populated
                    if sanitized_column not in unique_columns and pd.notna(value):
                        update_query = f"UPDATE {table_name} SET `{sanitized_column}` = %s WHERE " + " AND ".join([f"`{col}` = %s" for col in unique_columns])
                        cursor.execute(update_query, (value, *[row[col] for col in unique_columns]))
            else:
                # Insert the new row as usual
                columns = [f"`{col}`" for col in row.keys()]
                values = [row[col] for col in row.keys()]
                sql_insert = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(values))})"
                cursor.execute(sql_insert, values)

        conn.commit()

    except mysql.connector.Error as err:
        print(f"Error loading data into the database: {err}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()



@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return render_template('index.html', upload_message='No file part in the request')

    file = request.files['file']

    if file.filename == '':
        return render_template('index.html', upload_message='No selected file')

    if not allowed_file(file.filename):
        return render_template('index.html', upload_message='Invalid file type. Only .xlsx files are allowed.')

    # Save the uploaded file
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(file_path)

    # Load the Excel file into a pandas DataFrame
    try:
        df = pd.read_excel(file_path)

        if df.empty:
            return render_template('index.html', upload_message="Error: No columns found in the uploaded Excel file.")
    
    except Exception as e:
        return render_template('index.html', upload_message=f"Error reading Excel file: {str(e)}")

    json_data = excel_to_json(df)

    # Pass both json_data and df to insert_data
    insert_data(json_data, df)

    return render_template('index.html', upload_message="File uploaded and data loaded successfully!")

if __name__ == '__main__':
    app.run(debug=True)
