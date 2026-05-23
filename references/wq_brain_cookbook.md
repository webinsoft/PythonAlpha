# WorldQuant Brain Python Alpha Coding Cookbook

This guide details the strict coding conventions, constraints, and requirements for writing Python Alphas on the WorldQuant Brain platform.

---

## 1. Core Decorator & Function Structure

Every Alpha must be a single Python function decorated with the `@alpha` decorator.

### Basic Requirements
1. **Exactly one `@alpha` decorator** per function/submission.
2. The function **must accept exactly two parameters**: `data` and `store`.
3. The function **must return a 1-D `float32` NumPy array** of shape `[n_instruments]`.
4. Always convert/cast the return value: `return signal.astype(np.float32)`.

### Imports Allowed in the Submission File
```python
from brain.alphas import alpha
import numpy as np
import numpy.typing as npt
```
> [!IMPORTANT]
> **DO NOT** import other brain modules (e.g., `from brain import Brain`, `from brain.models import SimulationSettings`, or `BrainCache`) in the file defining the Alpha function. Including these imports will cause the simulation submission to fail.

---

## 2. Input Data handling (`data`)

The `data` parameter provides access to the declared input fields.

### Declaring Fields
Fields are declared as a list of strings in the `@alpha` decorator:
```python
@alpha(
    data=["returns", "close"],
    store=[]
)
def my_alpha(data, store) -> npt.NDArray[np.float32]:
    ...
```
Each field is a 2-D NumPy array of shape `[lookback_window + 1, n_instruments]`.
- The most recent time step is index `[-1]`.
- Yesterday is `[-2]`.

### Read-Only Constraints
Input data arrays are **read-only**. Trying to modify them in-place will raise a `ValueError`.
- **Incorrect:**
  ```python
  a = data.returns[-1]
  a[a < 0] = 0  # Raises ValueError
  ```
- **Correct:**
  ```python
  a = data.returns[-1].copy()
  a[a < 0] = 0  # Allowed
  ```

### The Special `universe` Field
- `data.universe` is always available as a 2-D integer array (`1` = in-universe, `0` = out-of-universe).
- **Do not** list `"universe"` in the `data` decorator list.

### Handling Integer Fields
Integer data fields (e.g. `int32`) cannot hold `NaN`. Missing values are represented by the type's minimum value (`-2147483648`).
To work with integer fields:
1. Cast the array to `float32`.
2. Retrieve the missing value sentinel via `get_missing_value(dtype)`.
3. Replace the sentinel values with `np.nan`.

```python
from brain import get_missing_value  # (Use only in local scripts, check submission context)
# For the submission itself:
# missing = -2147483648 (or type's min value)
# data_float = raw_data.astype(np.float32)
# data_float[raw_data == missing] = np.nan
```

### Price Adjustments (Corporate Actions)
Raw prices (`close`, `open`, `high`, `low`) and per-share fundamentals (`eps`, `book_value_ps`) contain split/dividend discontinuities. You must use `adjfactor` to adjust them before processing:
```python
adjusted_close = close / ((adjfactor - 1.0).cumsum() + 1.0)
```
*Note: The `returns` field is already split-adjusted and does not require this step.*

---

## 3. Persistent State management (`store`)

The `store` parameter is used to persist state across daily simulation steps.

### Declaration Types
1. **Untyped (String)**: Declared as plain strings in the `store` list. They pre-initialize to `None`.
   ```python
   @alpha(data=["returns"], store=["my_state"])
   ```
2. **Typed (Dict)**: Declared as dicts to define their shape, dimension behavior, and fill value.
   ```python
   store=[
       {"name": "running_mean", "dims": "i", "extend": np.float32(0.0)},
       {"name": "rank_cache", "dims": "xi", "extend": np.float32(np.nan)}
   ]
   ```

### Dimension Syntax (`dims`)
- `"i"`: Instruments axis (1-D array of size `n_instruments`). The simulator auto-extends this axis when new instruments enter the universe.
- `"x"`: Free axis (does not scale with the universe size).
- `"xi"`: 2-D array of shape `[any, n_instruments]`.
- `"ii"`: 2-D array of shape `[n_instruments, n_instruments]` (e.g. correlation matrix).

### Fill Values (`extend`)
> [!WARNING]
> When defining typed store items, the `extend` fill value must **exactly match** the array's dtype.
> - **Never** use bare python literals (like `0` or `0.0`) or bare `np.nan`. They will fail the type check.
> - Use NumPy scalar constructors: `np.float32(0)`, `np.float32(np.nan)`, `np.float64(np.nan)`.

### Initializing and Modifying State
Use `store.var is None` to detect the first simulation day:
```python
@alpha(
    data=["returns"],
    store=[{"name": "running_mean", "dims": "i", "extend": np.float32(0.0)}]
)
def smooth_alpha(data, store) -> npt.NDArray[np.float32]:
    if store.running_mean is None:
        store.running_mean = np.zeros(data.returns.shape[1], dtype=np.float32)
        
    raw = -np.nanmean(data.returns, axis=0)
    store.running_mean = 0.9 * store.running_mean + 0.1 * raw
    return store.running_mean.astype(np.float32)
```

---

## 4. Complete Compliant Template

Here is a standard, fully-compliant template for a Python Alpha submission:

```python
from brain.alphas import alpha
import numpy as np
import numpy.typing as npt

def pasteurize(a: npt.NDArray[np.float32], u: npt.NDArray) -> npt.NDArray[np.float32]:
    a = a.copy()
    a[~u.astype(bool)] = np.nan
    return a

def neutralize(a: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    a0 = np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0)
    return a - np.mean(a0)

def scale(a: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    a0 = np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0)
    norm = np.linalg.norm(a0, ord=1)
    return a / norm if norm > 0 else a

@alpha(
    data=["returns"],
    store=[]
)
def generate_alpha(data, store) -> npt.NDArray[np.float32]:
    # Extract today's returns
    today_returns = data.returns[-1]
    
    # Simple signal logic: mean reversion
    signal = -today_returns
    
    # Process signal
    signal = pasteurize(signal, data.universe[-1])
    signal = neutralize(signal)
    signal = scale(signal)
    
    # Ensure correct return type
    return signal.astype(np.float32)
```
