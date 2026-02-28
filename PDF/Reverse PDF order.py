from pypdf import PdfReader, PdfWriter

def reverse_pdf(input_filename, output_filename):
    # Initialize the reader and writer
    reader = PdfReader(input_filename)
    writer = PdfWriter()

    # Logic: Iterate through the page range in reverse
    # range(start, stop, step)
    num_pages = len(reader.pages)
    
    for i in range(num_pages - 1, -1, -1):
        writer.add_page(reader.pages[i])

    # Write the reversed array to a new file
    with open(output_filename, "wb") as output_file:
        writer.write(output_file)
    
    print(f"Success: {num_pages} pages reversed into {output_filename}")

# Usage
reverse_pdf("your_document.pdf", "reversed_document.pdf")
