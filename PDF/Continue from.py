#!/usr/bin/env python3

import sys

def generate_pages(total_pages):
    odd_pages = [p for p in range(1, total_pages + 1) if p % 2 == 1]
    even_pages = [p for p in range(1, total_pages + 1) if p % 2 == 0]
    return odd_pages, even_pages

def next_page(pages, printed_up_to):
    for p in pages:
        if p > printed_up_to:
            return p
    return None

def main():
    print("=== Page Printing Calculator ===")
    try:
        total_pages = int(input("Enter total number of pages: "))
    except ValueError:
        print("Invalid input. Please enter a number.")
        sys.exit(1)

    odd_pages, even_pages = generate_pages(total_pages)

    print(f"\nOdd pages: {odd_pages}")
    print(f"Even pages: {even_pages}")

    while True:
        try:
            printed_up_to = int(input("\nEnter the last page you printed (or 0 to quit): "))
        except ValueError:
            print("Invalid input. Please enter a number.")
            continue

        if printed_up_to == 0:
            print("Exiting calculator. Happy printing!")
            break

        # Ask whether user is continuing odds or evens
        mode = input("Are you continuing with odd or even pages? (odd/even): ").strip().lower()
        if mode == "odd":
            nextp = next_page(odd_pages, printed_up_to)
        elif mode == "even":
            nextp = next_page(even_pages, printed_up_to)
        else:
            print("Please type 'odd' or 'even'.")
            continue

        if nextp:
            print(f"Next {mode} page to continue: {nextp}")
        else:
            print(f"All {mode} pages are already printed.")

if __name__ == "__main__":
    main()
