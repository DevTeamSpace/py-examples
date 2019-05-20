class ClassRolloutDetailView(mixins.RetrieveModelMixin,
                             mixins.UpdateModelMixin,
                             mixins.DestroyModelMixin,
                             generics.GenericAPIView):
    queryset = ClassRollout.objects.all()
    permission_classes = (AllowAny,)
    serializer_class = ClassRolloutSerializer

    @check_active_session
    @check_permissions('teacher')
    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)

    @check_active_session
    @check_permissions('teacher')
    def put(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    @check_active_session
    @check_permissions('manager')
    def delete(self, request, *args, **kwargs):
        return self.destroy(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = ClassRolloutSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_destroy(serializer)
        return Response(serializer.data)

    def perform_destroy(self, serializer):
        """
        Delete class
        :param serializer:
        :return:
        """
        class_instance = serializer.instance
        instances = [class_instance]  # make iterable

        permanently = self.request.data.get('permanently', False)
        reason = self.request.data.get('reason', False)
        email_date = class_instance.class_date.strftime("%m/%d/%Y")

        # if 'permanently' get all class instances after chosen class
        if permanently:
            instances = class_instance.class_id\
                                      .class_rollout\
                                      .filter(class_date__gte=class_instance.class_date)\
                                      .order_by('class_date')

        for inst in instances:
            self.create_log(inst)
            self.cancel_class(inst, reason, permanently)

        # update google calendar events
        self.async_change_gc([self.delete_gc_event(instance) for instance in instances])

        # send teacher notification
        class_instance.send_delete_event_notification_email(
            class_instance, email_date, user=self.request.user.staff.full_name
        )

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = ClassRolloutSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return self.perform_update(serializer)

    def perform_update(self, serializer):
        """
        Instance of log will create for all updates
        :param serializer:
        :return:
        """
        class_instance = serializer.instance
        concurrences_data = {'unmodified': True}
        instances = [class_instance]
        student_instances = []

        # student flags
        flag_student_cancel = self.request.data.get('cancel_flag_student', False)
        flag_student_revert = self.request.data.get('revert_flag_student', False)
        flag_student_restore_in_class = self.request.data.get('restore_in_class_flag', False)
        flag_student_break = self.request.data.get('break_flag', False)
        flag_student_discontinued = self.request.data.get('discontinuation_flag', False)

        if flag_student_cancel:
            self.student_cancellation(class_instance)
            
        elif flag_student_revert:
            self.student_revert(class_instance)
            
        elif flag_student_restore_in_class:
            instances, student_instances = self.restore_break_process(class_instance)
            
        elif flag_student_break:
            instances, student_instances = self.break_process(class_instance)
            
        elif flag_student_discontinued:
            instances, student_instances = self.discontinuation_process(class_instance)

        else:
            instances, concurrences_data = self.regular_update(class_instance)

        if not instances:
            return Response(concurrences_data)

        # update google calendar
        update_events = [self.update_gc_event(instance) for instance in instances]

        if student_instances:
            update_student_events = [self.update_parent_gc_event(instance) for instance in student_instances]
            update_events += update_student_events

        self.async_change_gc(update_events)

        # send teacher notification
        class_instance.send_change_event_notification_email(
            class_instance, len(instances), user=self.request.user.staff.full_name
        )

        return Response(serializer.data)

    def async_change_gc(self, tasks):
        """
        Change google calendar events
        :param tasks: list of task
        :return:
        """
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)

        wait_tasks = asyncio.wait(tasks, return_when='FIRST_COMPLETED')

        event_loop.run_until_complete(wait_tasks)
        event_loop.close()

    async def update_gc_event(self, inst):
        """
        Update google calendar event
        :param inst:
        :return:
        """
        prepare_data_and_update_event(
            inst.gc_event_id, inst.location.calendarId,
            event_name=inst.gc_title,
            description=inst.gc_event_description,
            attendees=inst.gc_event_attendees,
            start_date=inst.class_date,
            start_time=inst.start_time,
            end_time=inst.end_time,
        )

    async def update_parent_gc_event(self, student_in_class):
        """
        Update google calendar event
        :param student_in_class:
        :return:
        """
        if student_in_class.gc_parent_event_id:
            prepare_data_and_update_event(
                student_in_class.gc_parent_event_id,
                student_in_class.class_id.location.parent_calendarId,
                event_name=student_in_class.gc_parent_title,
                description=student_in_class.gc_parent_event_description
            )

    async def delete_gc_event(self, inst):
        delete_gcalendar_event(inst.location.calendarId, inst.gc_event_id)

    def student_cancellation(self, inst):
        """
        Cancelled StudentInClass for specific student
        :param inst: Class Rollout
        :return:
        """
        student_id = self.request.data.get('student_id', None)
        student_in_class = StudentInClass.objects.filter(student__id=student_id, class_id=inst.id).first()

        if student_in_class:
            student_in_class.status = "cancelled"
            student_in_class.last_class = student_in_class.class_id
            student_in_class.class_id = None
            student_in_class.save()

            self.create_student_log(student_in_class)

            self.async_change_gc([self.update_parent_gc_event(student_in_class)])

    def student_revert(self, inst):
        """
        Reverted the StudentInClass for specific student
        :param inst: Class Rollout
        :return:
        """
        student_id = self.request.data.get('student_id', None)
        student_in_class = StudentInClass.objects.filter(student__id=student_id, last_class=inst.id).first()

        if student_in_class:
            student_in_class.status = "scheduled"
            student_in_class.class_id = student_in_class.last_class
            student_in_class.last_class = None
            student_in_class.save()

            if inst.max_capacity < inst.students.count():
                info = [{
                    'class_date': inst.class_date.strftime('%m/%d/%Y'),
                    'location': inst.location.short_name,
                    'duration': inst.duration.duration_short_name,
                    'subject': inst.subject.short_name,
                    'teacher': inst.staff.full_name
                }]

                inst.send_capacity_notification_email(
                    self.request.user.email, info,
                    self.request.user.staff.full_name
                )

            self.create_student_log(student_in_class)
            self.async_change_gc([self.update_parent_gc_event(student_in_class)])

    def discontinuation_process(self, class_instance):
        effective_date = convert_to_date(self.request.data.get('date', None))
        reason = self.request.data.get('reason', '')
        student_id = self.request.data.get('student_id', None)

        instances = class_instance.class_id.class_rollout\
                                           .filter(class_date__gte=effective_date)

        student_instances = StudentInClass.objects.filter(
            class_id__id__in=instances.values_list('id', flat=True),
            student__id=student_id)

        student_instances.update(
            status='discontinued',
            comments=reason,
            last_class=F('class_id'),
            class_id=None)

        return instances, student_instances

    def break_process(self, class_instance):
        start_date = convert_to_date(self.request.data.get('start_date', None))
        end_date = convert_to_date(self.request.data.get('end_date', None))
        student_id = self.request.data.get('student_id', None)
        reason = self.request.data.get('reason', None)

        instances = class_instance.class_id.class_rollout\
                                           .filter(class_date__gte=start_date,
                                                   class_date__lte=end_date)

        student_instances = StudentInClass.objects.filter(
            class_id__id__in=instances.values_list('id', flat=True),
            student__id=student_id)

        student_instances.update(
            status='break',
            comments=reason,
            status_comments="on break till {}".format(end_date.strftime("%b %d, %Y")),
            last_class=F('class_id'),
            class_id=None)

        return instances, student_instances

    def restore_break_process(self, class_instance):
        statuses = ['discontinued', 'break']
        start_date = convert_to_date(self.request.data.get('start_date', None))
        end_date = convert_to_date(self.request.data.get('end_date', None))
        student_id = self.request.data.get('student_id', None)

        # get all class rollout between date
        instances = class_instance.class_id.class_rollout\
                                           .filter(class_date__gte=start_date,
                                                   class_date__lte=end_date)

        student_instances = StudentInClass.objects.filter(
            last_class__id__in=instances.values_list('id', flat=True),
            student__id=student_id,
            status__in=statuses)

        if student_instances.filter(status='break').count():
            comments = student_instances.filter(status='break').values_list(
                'status_comments', flat=True
            ).distinct()

            for comment in comments:
                break_students_chain = StudentInClass.objects\
                                                     .filter(status_comments=comment)\
                                                     .exclude(last_class__class_date__gte=start_date,
                                                              last_class__class_date__lte=end_date)\
                                                     .order_by('last_class__class_date')

                if break_students_chain.count():
                    last_break = break_students_chain.last()
                    break_students_chain.update(status_comments="on break till {}".format(
                        last_break.last_class.class_date.strftime("%b %d, %Y"))
                    )

        student_instances.update(
            status='scheduled',
            comments='',
            status_comments="",
            class_id=F('last_class'),
            last_class=None)

        return instances, student_instances

    def regular_update(self, class_instance):
        """
        Regular update class rollout instance
        :param class_instance:
        :return:
        """
        permanently = self.request.data.get('permanently', False)
        max_students = int(self.request.data.get('max_students', 0))

        room_id = self.request.data.get('room', None)
        subject_id = self.request.data.get('subject', None)
        teacher_id = self.request.data.get('teacher', None)
        duration_id = self.request.data.get('duration', None)

        effective_date = convert_to_date(self.request.data.get('effective_date', None))
        class_date = convert_to_date(self.request.data.get('class_date', None))
        start_time = get_time(self.request.data.get('start_time', None))
        end_time = get_time(self.request.data.get('end_time', None))

        room = Room.objects.filter(id=room_id).first()
        subject = Subject.objects.filter(id=subject_id).first()
        staff = Staff.objects.filter(id=teacher_id).first()
        duration = ClassDuration.objects.filter(id=duration_id).first()

        concurrences_data = {}
        instances = [class_instance]
        class_dates = [class_date]
        statuses = ["scheduled", "present", "modified"]

        new_instances_params = {
            'max_students': max_students,
            'class_date': class_date,
            'start_time': start_time,
            'end_time': end_time,
            'duration': duration,
            'subject': subject,
            'teacher': staff,
            'room': room
        }

        # if 'permanently' get all class instances after chosen class
        if permanently:
            instances = class_instance.class_id\
                                      .class_rollout\
                                      .filter(class_date__gte=effective_date)\
                                      .order_by('class_date')

            for i in range(1, instances.count()):
                class_dates.append(class_dates[-1] + timedelta(weeks=1))

        # find classes intersecting by the time
        concurrences = ClassRollout.objects \
                                   .filter(class_date__in=class_dates, staff=teacher_id, class_status__in=statuses)\
                                   .filter(~Q(class_id=class_instance.class_id))\
                                   .filter(Q(start_time__lte=start_time, end_time__gt=start_time) |
                                           Q(start_time__gt=end_time, end_time__lte=end_time,))\
                                   .order_by('class_date')

        if concurrences.count():
            instances = []
            concurrent_class = concurrences.first()
            concurrences_data = {
                'unmodified': True,
                'count': concurrences.count(),
                'message': 'Teacher already has a class at this time. Please check info below:',
                'class': {
                    'date': concurrent_class.class_date,
                    'start_time': concurrent_class.start_time,
                    'end_time': concurrent_class.end_time,
                    'room': concurrent_class.room.room_name,
                    'teacher': concurrent_class.staff.full_name,
                    'subject': concurrent_class.subject.name
                }
            }

        for inst in instances:
            self.create_log(inst)
            self.change_instance(inst, new_instances_params)

            # for the permanently changes. event date every week
            new_instances_params['class_date'] += timedelta(weeks=1)

        return instances, concurrences_data

    def cancel_class(self, inst, reason, permanently):
        """
        Cancel class rollout class and all studentInClass instances
        :param inst:
        :param reason:
        :param status:
        :return:
        """
        inst.class_status = 'cancelled'
        inst.comments = reason
        inst.show_while_cancelled = not permanently
        inst.save()

        for student_in_class in inst.students.all():
            student_in_class.status = 'cancelled'
            student_in_class.last_class = student_in_class.class_id
            student_in_class.class_id = None
            student_in_class.save()

    def change_instance(self, inst, params):
        """
        Changed the class rollout instance
        :param inst:
        :param params:
        :return:
        """
        inst.class_date = params['class_date']
        inst.start_time = params['start_time']
        inst.end_time = params['end_time']
        inst.max_capacity = params['max_students']
        inst.room = params['room']
        inst.subject = params['subject']
        inst.staff = params['teacher']
        inst.duration = params['duration']
        inst.gc_event_title = inst.gc_title
        inst.class_status = 'modified'
        inst.save()

    def create_student_log(self, inst):
        """
        Create log for StudentInClass
        :param inst:
        :return:
        """

        student_log = StudentInClassLog()
        student_log.staff = self.request.user.staff
        student_log.class_instance = inst
        student_log.status = inst.status
        student_log.save()

    def create_log(self, inst):
        """
        Create log for the Class Rollout
        :param inst:
        :return:
        """
        log = ClassRolloutLog()
        log.modification_date = timezone.now()
        log.modification_object = inst
        log.modification_staff = self.request.user.staff

        log.staff = inst.staff
        log.room = inst.room
        log.subject = inst.subject
        log.location = inst.location
        log.duration = inst.duration
        log.class_id = inst.class_id

        log.max_capacity = inst.max_capacity
        log.class_status = inst.class_status

        log.created_by = inst.created_by
        log.create_date = inst.create_date

        log.start_time = inst.start_time
        log.end_time = inst.end_time
        log.class_date = inst.class_date
        log.comments = inst.comments

        log.gc_event_id = inst.gc_event_id
        log.gc_event_title = inst.gc_event_title

        log.save()
