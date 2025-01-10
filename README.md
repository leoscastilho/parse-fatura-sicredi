# Credit Card Expense Categorization and Processing

This Python project reads credit card expense data from Excel files, processes the data, categorizes the expenses, and exports the processed data into CSV files. The categorization of expenses can be done automatically using the OpenAI GPT-4 model or through a default category if AI integration is disabled. Additionally, the script handles special formatting, including handling negative values and ensuring they are placed at the bottom of the processed list.

## Features

- **Reads Excel Files**: Supports reading Excel files (`.xls`, `.xlsx`) containing credit card expenses.
- **Categorizes Expenses**: Automatically classifies expenses based on descriptions into predefined categories, using either a default category or the GPT-4 model.
- **Handles Negative Values**: Moves negative expense values to the bottom of the list.
- **Data Processing**: Cleans and formats data for consistency, including handling missing values, date formatting, and currency conversion.
- **Export to CSV**: Saves the processed data in CSV format.

## Requirements

- Python 3.6+
- Required Python packages:
    - `pandas`
    - `openai` (if using GPT-4 categorization)

To install the necessary packages, run:

```bash
pip install pandas openai
`````

## Setup

1. **Obtain OpenAI API Key**:  
   You will need an OpenAI API key to enable GPT-4-based categorization. Set your OpenAI API key by replacing the placeholder in the script:

   ```python
   openai.api_key = "your-api-key"
   ````

2. **Enable/Disable AI Categorization**:  
   You can enable or disable AI categorization by toggling the `enable_ai` flag at the beginning of the script:

   ```python
   enable_ai = True  # Set to False to disable AI categorization
   ````

3. **Input and Output Folders**:  
   Place your input Excel files in a folder (e.g., `input/`), and specify the output folder for the processed CSV files (e.g., `output/`).

## Usage

Run the script to process the credit card expense data:

```bash
python script_name.py
````

The script will:

1. Read all Excel files in the input folder.
2. Process the data to:
    - Categorize each expense (using either the AI model or a default category).
    - Normalize and format the columns (e.g., date and currency).
    - Move negative expense values to the bottom.
3. Save the processed data as CSV files in the output folder.

## Example

### Example Heading

### Input (Excel file):

| Data       | Descrição                   | Parcela | Valor (R$) |  
|------------|-----------------------------|---------|------------|  
| 01/01/2025 | Restaurante A                | 1       | 50,00      |  
| 01/01/2025 | Supermercado B               | 2       | -30,00     |  
| 01/01/2025 | Lazer Parque X               |         | 100,00     |  

### Output (CSV file):

| Data       | Descrição                   | Categoria       | Parcela | Valor (R$) | Pago |  
|------------|-----------------------------|-----------------|---------|------------|------|  
| 01/01/2025 | Restaurante A {em Jan}       | Alimentação     | 1       | 50.00      | x    |  
| 01/01/2025 | Supermercado B {em Jan}      | Alimentação     | 2       | 30.00      | x    |  
| 01/01/2025 | Lazer Parque X {em Jan}      | Lazer           |         | 100.00     | x    |  

### Notes:

- The **Categoria** column is filled based on the description (using GPT-4 if enabled).
- Negative values (e.g., `-30,00`) are moved to the bottom of the CSV file.

## Customization

### Modify Categories

You can customize the categories by modifying the `categories` list:

```python
categories = [
"Ajuste", "Alimentação", "Hobby", "Cachorro", "Casa", "Construção",
"Educação", "Eletrônicos", "Filha", "Imposto", "Investimento", "Lazer",
"Cartão de crédito", "Outros", "Poupança", "Renda Fixa", "Renda Variável",
"Resgate Poupança", "Restante", "Saúde", "Serviços", "Transporte", "Vestuário"
]
````

### Error Handling

The script includes error handling for various cases:
- If the category for a description cannot be determined (due to API issues or unknown descriptions), the default category "Outros" is used.
- If "Valor Total R$" is not found in the data, the script will notify you and default to the end of the table.
