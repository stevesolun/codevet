def get_element(lst, index):
    # Bug: Off-by-one error
    return lst[index + 1]

def average(numbers):
    # Bug: Division by zero when empty
    return sum(numbers) / len(numbers)
