"""Vista placeholder "En construcción".

Se monta en el área central del MainWindow cada vez que el usuario
hace click en un módulo cuya implementación real aún no existe
(empleados, turnos, sincronización, etc. — todo Phase 2+).

Cuando un módulo tenga implementación real, su ``MenuItem`` apuntará
a otra factory y este placeholder dejará de ser usado para ese code.
"""

from __future__ import annotations

import customtkinter as ctk


class PlaceholderView(ctk.CTkFrame):
    """Vista genérica que muestra un módulo aún no implementado."""

    def __init__(self, master: ctk.CTkBaseClass, title: str, description: str) -> None:
        """Construye el placeholder.

        Args:
            master: Frame/Widget padre donde se monta.
            title: Nombre del módulo (español).
            description: Texto descriptivo de qué contendrá.
        """
        super().__init__(master, corner_radius=0)

        titulo = ctk.CTkLabel(
            self,
            text=title,
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        titulo.pack(pady=(40, 8), padx=24, anchor="w")

        descripcion = ctk.CTkLabel(
            self,
            text=description,
            font=ctk.CTkFont(size=13),
            text_color=("gray30", "gray70"),
            wraplength=600,
            justify="left",
        )
        descripcion.pack(pady=(0, 24), padx=24, anchor="w")

        aviso = ctk.CTkLabel(
            self,
            text="Módulo en construcción — próximamente.",
            font=ctk.CTkFont(size=14, slant="italic"),
            text_color=("gray40", "gray60"),
        )
        aviso.pack(pady=12, padx=24, anchor="w")
