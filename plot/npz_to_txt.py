import numpy as np
import sys
import os

def npz_to_txt(npz_path):
    if not os.path.exists(npz_path):
        print(f"Error: File '{npz_path}' not found.")
        return

    data = np.load(npz_path)
    base_name = os.path.splitext(npz_path)[0]
    out_path = base_name + ".txt"

    # Ensure NumPy prints full arrays
    np.set_printoptions(threshold=sys.maxsize, linewidth=1000)

    with open(out_path, 'w') as f:
        for key in data.files:
            arr = data[key]
            f.write(f"[{key}]\n")
            
            if arr.ndim <= 2:
                import io
                s = io.StringIO()
                # Use fmt='%g' for compact but precise output
                np.savetxt(s, arr, fmt='%g')
                f.write(s.getvalue())
            else:
                # For 3D or higher, use NumPy's default string conversion but ensure it's full
                f.write(str(arr))
                f.write("\n")
            
            f.write("\n")

    print(f"Saved '{npz_path}' to '{out_path}'.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 npz_to_txt.py <file.npz>")
    else:
        npz_to_txt(sys.argv[1])
