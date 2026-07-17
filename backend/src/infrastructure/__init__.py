"""
CLOUD BANK — Infrastructure Layer

Los adaptadores implementan los puertos definidos en application/ y domain/.
Esta capa puede ser reemplazada sin modificar el dominio ni la aplicación.
Regla: ningún módulo fuera de infrastructure/ importa de infrastructure/.
"""
