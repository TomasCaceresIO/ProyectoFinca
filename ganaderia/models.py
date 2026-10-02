"""
Modelos de datos para el sistema de Gestión Ganadera MVP.
"""
from django.db import models
from django.core.validators import RegexValidator
from django.utils import timezone


class Explotacion(models.Model):
    """
    Explotación ganadera registrada oficialmente (Código REGA).
    """
    codigo_rega = models.CharField(
        max_length=20,
        unique=True,
        verbose_name='Código REGA',
        help_text='Código oficial de Registro de Explotaciones Agrarias'
    )
    nombre = models.CharField(
        max_length=200,
        verbose_name='Nombre de la explotación'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Explotación'
        verbose_name_plural = 'Explotaciones'

    def __str__(self):
        return f'{self.nombre} ({self.codigo_rega})'


class Finca(models.Model):
    """
    Finca perteneciente a una explotación.
    """
    explotacion = models.ForeignKey(
        Explotacion,
        on_delete=models.CASCADE,
        related_name='fincas',
        verbose_name='Explotación'
    )
    nombre = models.CharField(
        max_length=200,
        verbose_name='Nombre de la finca'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Finca'
        verbose_name_plural = 'Fincas'
        ordering = ['nombre']

    def __str__(self):
        return f'{self.nombre} ({self.explotacion.nombre})'


class Ubicacion(models.Model):
    """
    Recinto o sub-ubicación dentro de una finca (PASTO, CEBADERO, APARTADO).
    """
    TIPO_CHOICES = [
        ('PASTO', 'Pasto'),
        ('CEBADERO', 'Cebadero'),
        ('APARTADO', 'Apartado'),
    ]

    finca = models.ForeignKey(
        Finca,
        on_delete=models.CASCADE,
        related_name='ubicaciones',
        verbose_name='Finca'
    )
    tipo_ubicacion = models.CharField(
        max_length=10,
        choices=TIPO_CHOICES,
        verbose_name='Tipo de ubicación'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Ubicación'
        verbose_name_plural = 'Ubicaciones'
        ordering = ['finca', 'tipo_ubicacion']

    def __str__(self):
        return f'{self.get_tipo_ubicacion_display()} - {self.finca.nombre}'


class Animal(models.Model):
    """
    Animal registrado en la explotación.
    """
    SEXO_CHOICES = [
        ('M', 'Macho'),
        ('H', 'Hembra'),
    ]
    ESTADO_VITAL_CHOICES = [
        ('VIVO', 'Vivo'),
        ('BAJA', 'Baja'),
    ]
    SUB_UBICACION_CHOICES = [
        ('PASTO', 'Pasto'),
        ('CEBADERO', 'Cebadero'),
        ('APARTADO', 'Apartado'),
    ]

    crotal = models.CharField(
        max_length=4,
        verbose_name='Crotal',
        validators=[
            RegexValidator(
                regex=r'^\d{4}$',
                message='El crotal debe ser exactamente 4 dígitos numéricos'
            )
        ]
    )
    sexo = models.CharField(
        max_length=1,
        choices=SEXO_CHOICES,
        verbose_name='Sexo'
    )
    fecha_nacimiento = models.DateField(
        verbose_name='Fecha de nacimiento'
    )
    raza = models.CharField(
        max_length=50,
        default='CRUZADO',
        verbose_name='Raza'
    )
    estado_vital = models.CharField(
        max_length=5,
        choices=ESTADO_VITAL_CHOICES,
        default='VIVO',
        verbose_name='Estado vital'
    )
    fecha_baja = models.DateField(
        null=True,
        blank=True,
        verbose_name='Fecha de baja'
    )
    motivo_baja = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Motivo de baja'
    )
    finca = models.ForeignKey(
        Finca,
        on_delete=models.CASCADE,
        related_name='animales',
        verbose_name='Finca'
    )
    sub_ubicacion = models.CharField(
        max_length=10,
        choices=SUB_UBICACION_CHOICES,
        default='PASTO',
        verbose_name='Sub-ubicación (recinto)'
    )
    fecha_entrada_cebadero = models.DateField(
        null=True,
        blank=True,
        verbose_name='Fecha de entrada al cebadero'
    )
    madre = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='hijos',
        verbose_name='Madre'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Animal'
        verbose_name_plural = 'Animales'
        ordering = ['crotal']
        constraints = [
            models.UniqueConstraint(
                fields=['crotal'],
                condition=models.Q(estado_vital='VIVO'),
                name='unique_crotal_vivo'
            )
        ]

    def __str__(self):
        return f'Crotal {self.crotal} ({self.get_sexo_display()}) - {self.get_estado_vital_display()}'

    @property
    def edad_dias(self):
        """Edad en días desde el nacimiento."""
        return (timezone.now().date() - self.fecha_nacimiento).days

    @property
    def es_reproductora(self):
        """Hembra que ha tenido al menos un parto."""
        return self.sexo == 'H' and self.partos_como_madre.exists()

    @property
    def ultimo_parto(self):
        """Último parto registrado de esta hembra (optimizado para prefetch)."""
        if hasattr(self, '_prefetched_objects_cache') and 'partos_como_madre' in self._prefetched_objects_cache:
            partos = list(self.partos_como_madre.all())
            if partos:
                return max(partos, key=lambda p: p.fecha_parto)
            return None
        return self.partos_como_madre.order_by('-fecha_parto').first()

    @property
    def dias_desde_ultimo_parto(self):
        """Días transcurridos desde el último parto."""
        up = self.ultimo_parto
        if up and up.fecha_parto:
            return (timezone.now().date() - up.fecha_parto).days
        return None


class Parto(models.Model):
    """
    Registro de parto de una hembra (1 o 2 crías permitidas).
    """
    madre = models.ForeignKey(
        Animal,
        on_delete=models.CASCADE,
        related_name='partos_como_madre',
        verbose_name='Madre'
    )
    fecha_parto = models.DateField(
        verbose_name='Fecha del parto'
    )
    cria = models.ForeignKey(
        Animal,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='parto_origen',
        verbose_name='Cría 1'
    )
    cria2 = models.ForeignKey(
        Animal,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='parto_origen_segundo',
        verbose_name='Cría 2 (Gemelar)'
    )
    alerta_intervalo = models.BooleanField(
        default=False,
        verbose_name='Alerta de intervalo (< 270 días)'
    )
    observaciones = models.TextField(
        blank=True,
        verbose_name='Observaciones'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Parto'
        verbose_name_plural = 'Partos'
        ordering = ['-fecha_parto']

    def __str__(self):
        return f'Parto de {self.madre.crotal} el {self.fecha_parto}'


class Incidencia(models.Model):
    """
    Registro de incidencias/alertas.
    """
    TIPO_CHOICES = [
        ('ROJO', 'Alerta Roja - Intervalo Parto < 270 días'),
        ('AMARILLO', 'Alerta Amarilla - Crotal histórico reutilizado'),
        ('INFO', 'Información'),
    ]

    animal = models.ForeignKey(
        Animal,
        on_delete=models.CASCADE,
        related_name='incidencias',
        verbose_name='Animal'
    )
    tipo = models.CharField(
        max_length=10,
        choices=TIPO_CHOICES,
        verbose_name='Tipo de incidencia'
    )
    descripcion = models.TextField(
        verbose_name='Descripción'
    )
    parto = models.ForeignKey(
        Parto,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='incidencias',
        verbose_name='Parto relacionado'
    )
    resuelta = models.BooleanField(
        default=False,
        verbose_name='Resuelta'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Incidencia'
        verbose_name_plural = 'Incidencias'
        ordering = ['-created_at']

    def __str__(self):
        return f'[{self.get_tipo_display()}] Animal {self.animal.crotal}'
