import fitz  # PyMuPDF
from PIL import Image
import io

def remove_internal_horizontal_gaps(input_pdf_path, output_pdf_path, white_threshold=245, min_gap_height=15):
    doc = fitz.open(input_pdf_path)
    final_pages = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        
        # Render high-res image of page
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        gray = img.convert("L")
        
        width, height = img.size
        
        # Determine which horizontal rows contain content vs blank space
        row_has_content = []
        for y in range(height):
            # Sample row pixels across width
            row_pixels = [gray.getpixel((x, y)) for x in range(0, width, 2)]
            # If any pixel in the row is darker than threshold, row has content
            row_has_content.append(any(p <= white_threshold for p in row_pixels))

        # Identify contiguous blocks of content
        content_blocks = []
        in_block = False
        start_y = 0
        
        for y, has_content in enumerate(row_has_content):
            if has_content and not in_block:
                in_block = True
                start_y = y
            elif not has_content and in_block:
                # Check if gap is large enough to warrant stripping
                gap_size = 0
                temp_y = y
                while temp_y < height and not row_has_content[temp_y]:
                    gap_size += 1
                    temp_y += 1
                
                if gap_size >= min_gap_height or temp_y == height:
                    in_block = False
                    content_blocks.append((start_y, y))

        if in_block:
            content_blocks.append((start_y, height))

        if not content_blocks:
            continue

        # Crop out content slices and stack them vertically
        slices = [img.crop((0, y1, width, y2)) for y1, y2 in content_blocks]
        total_height = sum(s.height for s in slices)
        
        stacked_page = Image.new("RGB", (width, total_height), (255, 255, 255))
        current_y = 0
        for s in slices:
            stacked_page.paste(s, (0, current_y))
            current_y += s.height

        final_pages.append(stacked_page)

    if final_pages:
        final_pages[0].save(
            output_pdf_path,
            save_all=True,
            append_images=final_pages[1:],
            format="PDF"
        )
        print("Done! Gaps removed.")

if __name__ == "__main__":
    remove_internal_horizontal_gaps("fat.pdf", "fat_compact.pdf")