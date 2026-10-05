"""``calculator`` tool: basic arithmetic and number checks."""

import math

from ..core.tool import Tool
from ..infra.app_logging import log


class CalculatorTool(Tool):
    """Arithmetic for the model: + - * /, powers, factorial, primality and square roots."""

    @staticmethod
    def spec():
        return {
            "type": "function",
            "function": {
                "name": "calculator",
                "description": (
                    "Provides add, subtract, multiply, divide, exponentiate, factorial, is_prime, "
                    "square_root"
                ),
                "parameters": {
                    "type": "object",
                    "required": ["command", "argument1"],
                    "properties": {
                        "command": {
                            "type": "string",
                            "enum": [
                                "add",
                                "subtract",
                                "multiply",
                                "divide",
                                "exponentiate",
                                "factorial",
                                "is_prime",
                                "square_root",
                            ],
                            "description": "The operation to perform",
                        },
                        "argument1": {
                            "type": "number",
                            "description": "The first argument",
                        },
                        "argument2": {
                            "type": "number",
                            "description": (
                                "Second argument (not needed for factorial, is_prime, "
                                "square_root)"
                            ),
                        },
                    },
                },
            },
        }

    @staticmethod
    def add(a: int, b: int) -> int:
        """Return ``a + b``."""
        return a + b

    @staticmethod
    def subtract(a: int, b: int) -> int:
        """Return ``a - b``."""
        return a - b

    @staticmethod
    def multiply(a: int, b: int) -> int:
        """Return ``a * b``."""
        return a * b

    @staticmethod
    def divide(a: int, b: int) -> float:
        """Return ``a / b``; raises ``ValueError`` if ``b`` is 0."""
        if b == 0:
            raise ValueError("Division by zero is not allowed")
        return a / b

    @staticmethod
    def exponentiate(a: float, b: float) -> float:
        """Return ``a`` to the power ``b``."""
        result = math.pow(a, b)
        return result

    @staticmethod
    def factorial(n: int) -> int:
        """Return ``n!``; raises ``ValueError`` if ``n`` is negative."""
        if n < 0:
            raise ValueError("Attempt to calculate factorial of a negative number")
        return math.factorial(n)

    @staticmethod
    def is_prime(n: int) -> bool:
        """Return True if ``n`` is prime (trial division)."""
        if n <= 1:
            return False
        for i in range(2, int(math.sqrt(n)) + 1):
            if n % i == 0:
                return False

        return True

    @staticmethod
    def square_root(n: float) -> float:
        """Return the square root of ``n``; raises ``ValueError`` if ``n`` is negative."""
        if n < 0:
            raise ValueError("Attempt to calculate square root of a negative number")

        return math.sqrt(n)

    @staticmethod
    def call(command: str, argument1: int, argument2: int = None) -> str:
        """Apply ``command`` to the arguments and return the result as text.

        ``argument2`` is only used by the two-operand commands; errors are returned
        as text.
        """
        log.info("calculator, command: %s", command)

        try:
            if command == "add":
                result = CalculatorTool.add(argument1, argument2)

            elif command == "subtract":
                result = CalculatorTool.subtract(argument1, argument2)

            elif command == "multiply":
                result = CalculatorTool.multiply(argument1, argument2)

            elif command == "divide":
                if argument2 == 0:
                    return "Error: Division by zero"
                result = CalculatorTool.divide(argument1, argument2)

            elif command == "exponentiate":
                result = CalculatorTool.exponentiate(argument1, argument2)

            elif command == "factorial":
                result = CalculatorTool.factorial(argument1)

            elif command == "is_prime":
                result = CalculatorTool.is_prime(argument1)

            elif command == "square_root":
                result = CalculatorTool.square_root(argument1)

            else:
                return f"Error: Unknown command '{command}'"

            return str(result)

        except Exception as e:  # pylint: disable=broad-exception-caught  # error goes back to the model
            log.error("Error executing command '%s': %s", command, e)
            return f"Error executing command: {e}"
