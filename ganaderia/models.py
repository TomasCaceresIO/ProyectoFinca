"""
Modelos de datos para el sistema de Gestión Ganadera MVP.

Modelo relacional:
- Explotacion (1) -- (N) Finca
- Finca (1) -- (N) Ubicacion  
- Ubicacion (1) -- (N) Animal
- Animal autorreferenciado (madre -> cría)
- Animal (1) -- (N) Parto (como madre)
"""
from django.db import models
from django.core.validators import RegexValidator, MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone


class Explotacion(models.Model):
    """
    Explotación ganadera registrada oficialmente.
    Solo puede existir una por instalación (Onboarding Wizard).
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
    Finca o parcela perteneciente a una explotación.
    Una explotación puede tener varias fincas.
    """
    explotacion = models.ForeignKey(
        Explotacion,
        on_delete=models.PROTECT,
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
    Recinto o sub-ubicación dentro de una finca.
    Tipos: PASTO (campo abierto), CEBADERO (engorde), APARTADO (separados).
    """
    TIPO_CHOICES = [
        ('PASTO', 'Pasto'),
        ('CEBADERO', 'Cebadero'),
        ('APARTADO', 'Apartado'),
    ]

    finca = models.ForeignKey(
        Finca,
        on_delete=models.PROTECT,
        related_name='ubicaciones',
        verbose_name='Finca'
    )
    tipo = models.CharField(
        max_length=10,
        choices=TIPO_CHOICES,
        verbose_name='Tipo de ubicación'
    )
    # Campo calculado / de caché para mostrar en UI sin contar siempre
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Ubicación'
        verbose_name_plural = 'Ubicaciones'
        ordering = ['finca', 'tipo']

    def __str__(self):
        return f'{self.get_tipo_display()} - {self.finca.nombre}'

    @property
    def n_animales(self):
        """Número de animales vivos en esta ubicación."""
        return self.animales.filter(estado_vital='VIVO').count()


class Animal(models.Model):
    """
    Animal registrado en la explotación.
    
    Reglas de negocio clave:
    - crotal: exactamente 4 dígitos numéricos (RN-01)
    - Solo puede haber UN animal VIVO con el mismo crotal (unicidad activa, RN-02)
    - Los animales dados de baja conservan el crotal pero estado_vital='BAJA' (RN-03)
    - fecha_entrada_cebadero se asigna automáticamente al trasladar a CEBADERO
    """
    SEXO_CHOICES = [
        ('M', 'Macho'),
        ('H', 'Hembra'),
    ]
    ESTADO_VITAL_CHOICES = [
        ('VIVO', 'Vivo'),
        ('BAJA', 'Baja'),
    ]
    RAZA_CHOICES = [
        ('RETINTA', 'Retinta'),
        ('CHAROLESA', 'Charolesa'),
        ('LIMUSINA', 'Limusina'),
        ('FRISONA', 'Frisona'),
        ('SIMMENTAL', 'Simmental'),
        ('ANGUS', 'Angus'),
        ('HEREFORD', 'Hereford'),
        ('CRUZADO', 'Cruzado'),
        ('OTRA', 'Otra'),
    ]

    # Identificación
    crotal = models.CharField(
        max_length=4,
        verbose_name='Crotal (número de identificación)',
        validators=[
            RegexValidator(
                regex=r'^\d{4}$',
                message='El crotal debe ser exactamente 4 dígitos numéricos'
            )
        ]
    )
    
    # Datos básicos
    sexo = models.CharField(
        max_length=1,
        choices=SEXO_CHOICES,
        verbose_name='Sexo'
    )
    fecha_nacimiento = models.DateField(
        verbose_name='Fecha de nacimiento'
    )
    raza = models.CharField(
        max_length=20,
        choices=RAZA_CHOICES,
        default='CRUZADO',
        verbose_name='Raza'
    )
    
    # Estado
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
    
    # Ubicación actual
    finca_actual = models.ForeignKey(
        Finca,
        on_delete=models.PROTECT,
        related_name='animales',
        verbose_name='Finca actual'
    )
    sub_ubicacion = models.ForeignKey(
        Ubicacion,
        on_delete=models.PROTECT,
        related_name='animales',
        verbose_name='Sub-ubicación (recinto)'
    )
    
    # Cebadero
    fecha_entrada_cebadero = models.DateField(
        null=True,
        blank=True,
        verbose_name='Fecha de entrada al cebadero'
    )
    
    # Maternidad (autorreferencia)
    madre = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='crias',
        verbose_name='Madre',
        limit_choices_to={'sexo': 'H', 'estado_vital': 'VIVO'}
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Animal'
        verbose_name_plural = 'Animales'
        ordering = ['crotal']
        # Restricción: solo 1 animal VIVO por crotal
        constraints = [
            models.UniqueConstraint(
                fields=['crotal'],
                condition=models.Q(estado_vital='VIVO'),
                name='unique_crotal_vivo'
            )
        ]

    def __str__(self):
        return f'Crotal {self.crotal} ({self.get_sexo_display()}) - {self.get_estado_vital_display()}'

    def clean(self):
        """Validaciones a nivel de modelo."""
        super().clean()
        # Validar que la sub_ubicacion pertenece a la finca_actual
        if self.finca_actual_id and self.sub_ubicacion_id:
            if self.sub_ubicacion.finca_id != self.finca_actual_id:
                raise ValidationError(
                    'La sub-ubicación no pertenece a la finca seleccionada.'
                )
        # No puede ser su propia madre
        if self.madre_id and self.pk and self.madre_id == self.pk:
            raise ValidationError('Un animal no puede ser su propia madre.')

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
        """Último parto registrado de esta hembra."""
        return self.partos_como_madre.order_by('-fecha_parto').first()


class Parto(models.Model):
    """
    Registro de parto de una hembra.
    
    - alerta_intervalo: True si el intervalo con el parto anterior fue < 270 días (RN-04)
    - La cría puede no registrarse inmediatamente (nullable)
    """
    madre = models.ForeignKey(
        Animal,
        on_delete=models.PROTECT,
        related_name='partos_como_madre',
        verbose_name='Madre',
        limit_choices_to={'sexo': 'H'}
    )
    fecha_parto = models.DateField(
        verbose_name='Fecha del parto'
    )
    cria = models.OneToOneField(
        Animal,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='parto_origen',
        verbose_name='Cría'
    )
    alerta_intervalo = models.BooleanField(
        default=False,
        verbose_name='Alerta de intervalo',
        help_text='True si el intervalo entre partos fue menor a 270 días'
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
        return f'Parto de {self.madre} el {self.fecha_parto}'


class Incidencia(models.Model):
    """
    Registro de incidencias/alertas generadas por las reglas de negocio.
    Se generan automáticamente, el usuario puede marcarlas como resueltas.
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
        return f'[{self.get_tipo_display()}] Animal {self.animal.crotal} - {self.created_at.date()}'
