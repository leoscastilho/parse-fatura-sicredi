import pandas as pd
import os
from datetime import datetime
import openai

# Initialize OpenAI API (make sure to set your OpenAI API key)
openai.api_key = os.getenv("OPENAI_API_KEY")
openai_model = "gpt-4o-mini"
enable_ai = False

# List of possible categories
categories = [
    "Ajuste", "Alimentação", "Hobby", "Cachorro", "Casa", "Construção",
    "Educação", "Eletrônicos", "Filha", "Imposto", "Investimento", "Lazer",
    "Cartão de crédito", "Outros", "Poupança", "Renda Fixa", "Renda Variável",
    "Resgate Poupança", "Restante", "Saúde", "Serviços", "Transporte", "Vestuário"
]


def get_category_from_description(description):
    if not enable_ai:
        return "Outros"  # Return default category if AI is disabled

    prompt = f"Given the description: '{description}', classify it into one of the following categories: {', '.join(categories)}."

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",  # Specify GPT-4 model (or the specific variant)
            messages=[
                {"role": "system", "content": "You are a helpful assistant that categorizes expenses."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=20,
            temperature=0.3,  # Lower temperature for more consistent results
        )
        category = response['choices'][0]['message']['content'].strip()

        # Ensure the category is valid
        if category not in categories:
            category = "Outros"  # Default category if it's not recognized
    except Exception as e:
        print(f"Error with GPT-4 API: {e}")
        category = "Outros"  # Default category in case of an error

    return category

def read_credit_card_table(input_folder, output_folder):
    # Ensure output folder exists
    os.makedirs(output_folder, exist_ok=True)

    # Get the first day of the current month
    first_day_of_current_month = datetime(datetime.now().year, datetime.now().month, 1).strftime("%m/%d/%Y")

    # Iterate through all files in the input folder
    for file_name in os.listdir(input_folder):
        # Check if the file has an Excel extension
        if file_name.endswith(".xls") or file_name.endswith(".xlsx"):
            input_xls = os.path.join(input_folder, file_name)
            output_csv = os.path.join(output_folder, f"{os.path.splitext(file_name)[0]}.csv")

            # Read the Excel file
            xls = pd.ExcelFile(input_xls)

            # Assuming the relevant data is in the first sheet
            sheet_name = xls.sheet_names[0]
            data = pd.read_excel(xls, sheet_name=sheet_name, header=None)

            # Find the header row ("Histórico de Despesas")
            header_row = data.apply(lambda row: row.str.contains("Histórico de Despesas", na=False).any(), axis=1).idxmax()

            # Extract the table starting from the header row
            table_start = header_row + 2  # Skip "Histórico de Despesas" and "Despesas no Brasil"
            table = data.loc[table_start:]

            # Find the row where "Valor Total R$" is located (this approach checks all columns)
            end_row = table.apply(lambda row: row.astype(str).str.strip().str.contains("Valor Total", na=False).any(), axis=1)

            # Check if "Valor Total R$" is found and get the index of the first occurrence
            if end_row.any():
                end_row = end_row.idxmax()  # Get the index of the first True
            else:
                print("Error: 'Valor Total R$' not found")
                end_row = len(table)  # Default to the end of the table if not found

            # Table data is before the "Valor Total R$" row
            table = table.loc[:end_row-1]

            # Set the header row for the table
            table.columns = data.loc[table_start]
            table = table[1:]  # Drop the header row itself from the data

            # Add empty "Categoria" column between "Data" and "Descrição"
            if "Data" in table.columns:
                table.insert(table.columns.get_loc("Descrição"), "Categoria", "")

            # Use GPT-4 to categorize each description before manipulation, if AI is enabled
            if "Descrição" in table.columns and enable_ai:
                table["Categoria"] = table["Descrição"].apply(get_category_from_description)

            # Add a new column "Pago" with "x" to all rows
            table["Pago"] = "x"

            # Process "Descrição" column with original "Data" for concatenation
            if "Parcela" in table.columns and "Descrição" in table.columns and "Data" in table.columns:
                # Concatenate "[Cartão]" to the beginning of "Descrição" and "Parcela" if applicable
                table["Descrição"] = table.apply(
                    lambda row: f"[Cartão] {row['Descrição'].strip()} (Parcela {row['Parcela']})"
                    if pd.notna(row["Parcela"]) else f"[Cartão] {row['Descrição'].strip()}",
                    axis=1
                )

                # Add the original "Data" to "Descrição"
                def format_date_and_append(row):
                    try:
                        # Use the original "Data" date for the concatenation
                        date_obj = datetime.strptime(row["Data"], "%d/%m/%Y")
                        formatted_date = f"{date_obj.day}/{date_obj.strftime('%b')}"
                        return f"{row['Descrição']} {{em {formatted_date}}}"
                    except (ValueError, TypeError):
                        return row["Descrição"]

                table["Descrição"] = table.apply(format_date_and_append, axis=1)

                # Optionally remove the "Parcela" column
                table = table.drop(columns=["Parcela"])

            # Normalize the "Descrição" column (convert to title case)
            if "Descrição" in table.columns:
                table["Descrição"] = table["Descrição"].apply(lambda x: x.title() if isinstance(x, str) else x)

            # Update "Data" column with the first day of the current month
            if "Data" in table.columns:
                table["Data"] = first_day_of_current_month

            # Process "Valor (R$)" column
            if "Valor (R$)" in table.columns:
                table["Valor (R$)"] = table["Valor (R$)"].replace(
                    {r"[^\d,.-]": "", r"\.": "", ",": "."}, regex=True
                ).astype(float)

                # Move negative values to the bottom by sorting
                table = table.sort_values(by="Valor (R$)", ascending=[False])

            # Save to a new CSV file
            table.to_csv(output_csv, index=False, encoding="utf-8")
            print(f"Processed {file_name} -> {output_csv}")

# Example usage
read_credit_card_table("input", "output")
