from django.test import TestCase
from ganaderia.services.ai_assistant import _extraer_tabla_pdf_dinamica, _extraer_lote_regex


class DynamicPDFParserTestCase(TestCase):
    """
    Tests para la extracción y parseo heurístico dinámico de tablas de PDF de censo.
    Verifica que el orden de columnas arbitrario/variable se mapee correctamente
    y que los crotales de 4 dígitos no se confundan con años, números de orden o fechas.
    """

    def test_orden_columnas_formato_a(self):
        """
        Orden de columnas Formato A:
        Nº | Crotal | Sexo | Fecha Nac | Raza | Madre | Ubicación
        """
        texto_pdf_a = """
        LISTADO OFICIAL DE CENSO BOVINO - EXPLOTACIÓN EL ROBLEDO
        Fecha de emisión: 12/05/2026

        Nº | Crotal | Sexo | Fecha Nac | Raza | Madre | Ubicación
        1  | 1001   | H    | 15/03/2021| Limusina | 0050  | Pasto
        2  | 1002   | M    | 20/07/2022| Retinta  | Sin madre | Cebadero
        3  | 1003   | Hembra | 05/11/2023| Charolesa | -    | Pasto
        4  | 1004   | Macho  | 10/01/2024| Limusina | 0055  | Apartado
        """
        animales = _extraer_tabla_pdf_dinamica(texto_pdf_a)
        self.assertEqual(len(animales), 4)

        # Registro 1
        a1 = animales[0]
        self.assertEqual(a1["crotal"], "1001")
        self.assertEqual(a1["sexo"], "H")
        self.assertEqual(a1["fecha_nacimiento"], "2021-03-15")
        self.assertEqual(a1["raza"], "Limusina")
        self.assertEqual(a1["crotal_madre"], "0050")
        self.assertEqual(a1["sub_ubicacion"], "PASTO")

        # Registro 2 (Fundador sin madre)
        a2 = animales[1]
        self.assertEqual(a2["crotal"], "1002")
        self.assertEqual(a2["sexo"], "M")
        self.assertEqual(a2["fecha_nacimiento"], "2022-07-20")
        self.assertEqual(a2["raza"], "Retinta")
        self.assertIsNone(a2["crotal_madre"])
        self.assertEqual(a2["sub_ubicacion"], "CEBADERO")

        # Registro 3
        a3 = animales[2]
        self.assertEqual(a3["crotal"], "1003")
        self.assertEqual(a3["sexo"], "H")
        self.assertEqual(a3["fecha_nacimiento"], "2023-11-05")
        self.assertEqual(a3["raza"], "Charolesa")
        self.assertIsNone(a3["crotal_madre"])
        self.assertEqual(a3["sub_ubicacion"], "PASTO")

        # Registro 4
        a4 = animales[3]
        self.assertEqual(a4["crotal"], "1004")
        self.assertEqual(a4["sexo"], "M")
        self.assertEqual(a4["fecha_nacimiento"], "2024-01-10")
        self.assertEqual(a4["raza"], "Limusina")
        self.assertEqual(a4["crotal_madre"], "0055")
        self.assertEqual(a4["sub_ubicacion"], "APARTADO")

    def test_orden_columnas_formato_b(self):
        """
        Orden de columnas Formato B:
        Crotal | Raza | Sexo | Madre | Ubicación | F. Nacimiento
        """
        texto_pdf_b = """
        INVENTARIO DE ANIMALES
        Explotación Ganadera Monteverde

        Crotal | Raza | Sexo | Madre | Ubicación | F. Nacimiento
        2001   | Retinta  | M    | 0012  | Cebadero  | 14/02/2020
        2002   | Limusina | H    | Fundadora | Pasto | 28/08/2021
        2003   | Charolesa| Hembra| 0044 | Pasto     | 01/12/2022
        """
        animales = _extraer_tabla_pdf_dinamica(texto_pdf_b)
        self.assertEqual(len(animales), 3)

        # Registro 1
        b1 = animales[0]
        self.assertEqual(b1["crotal"], "2001")
        self.assertEqual(b1["raza"], "Retinta")
        self.assertEqual(b1["sexo"], "M")
        self.assertEqual(b1["crotal_madre"], "0012")
        self.assertEqual(b1["sub_ubicacion"], "CEBADERO")
        self.assertEqual(b1["fecha_nacimiento"], "2020-02-14")

        # Registro 2 (Fundadora)
        b2 = animales[1]
        self.assertEqual(b2["crotal"], "2002")
        self.assertEqual(b2["raza"], "Limusina")
        self.assertEqual(b2["sexo"], "H")
        self.assertIsNone(b2["crotal_madre"])
        self.assertEqual(b2["sub_ubicacion"], "PASTO")
        self.assertEqual(b2["fecha_nacimiento"], "2021-08-28")

        # Registro 3
        b3 = animales[2]
        self.assertEqual(b3["crotal"], "2003")
        self.assertEqual(b3["raza"], "Charolesa")
        self.assertEqual(b3["sexo"], "H")
        self.assertEqual(b3["crotal_madre"], "0044")
        self.assertEqual(b3["sub_ubicacion"], "PASTO")
        self.assertEqual(b3["fecha_nacimiento"], "2022-12-01")

    def test_crotales_exactos_4_digitos_no_confunde_fechas_ni_orden(self):
        """
        Verifica que en ambos formatos los crotales extraídos sean numéricos exactos de 4 dígitos,
        las fechas de nacimiento se mantengan en sus valores históricos y no se confundan con crotales.
        """
        texto_con_ruido = """
        Nº \t Identificador \t Fecha \t Sexo \t Raza \t Dam \t Destino
        12 \t 4500 \t 12/10/2023 \t H \t Limusina \t 3000 \t Pasto
        13 \t 0045 \t 01/01/2024 \t M \t Retinta \t - \t Pasto
        """
        resultado = _extraer_lote_regex(texto_con_ruido)
        self.assertEqual(resultado["intencion"], "IMPORTAR_LOTE")
        self.assertEqual(len(resultado["animales"]), 2)

        a1 = resultado["animales"][0]
        self.assertEqual(a1["crotal"], "4500")
        self.assertNotEqual(a1["crotal"], "2023")  # No confunde año con crotal
        self.assertNotEqual(a1["crotal"], "0012")  # No toma el Nº de orden
        self.assertEqual(a1["fecha_nacimiento"], "2023-10-12")

        a2 = resultado["animales"][1]
        self.assertEqual(a2["crotal"], "0045")
        self.assertNotEqual(a2["crotal"], "2024")
        self.assertEqual(a2["fecha_nacimiento"], "2024-01-01")
        self.assertIsNone(a2["crotal_madre"])
